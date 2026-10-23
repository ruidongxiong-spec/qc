import json
import threading
import unittest
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer

from scope.backends import DemoBackend, OpenAIBackend
from scope.core import Config, Engine, Node, Plan, Verdict
from scope.evaluation import score
from scope.tools import DemoTools, arithmetic


class RecordingBackend(DemoBackend):
    def __init__(self, verdicts=()):
        self.verdicts = iter(verdicts)
        self.calls = []

    def call(self, role, payload, temperature):
        # Freeze inputs so subsequent state mutations cannot hide propagation bugs.
        self.calls.append((role, json.loads(json.dumps(payload)), temperature))
        if role == "verifier":
            verdict = next(self.verdicts, "Pass")
            return {"verdict": verdict, "reason": "Need evidence"}
        return super().call(role, payload, temperature)


class ScopeTests(unittest.TestCase):
    def test_invalid_synthesis_is_recorded_as_abstention(self):
        for invalid in ({}, {"answer": " "}, {"answer": None}, []):
            with self.subTest(invalid=invalid):
                class InvalidSynthesis(DemoBackend):
                    def call(self, role, payload, temperature):
                        if role == "synthesizer":
                            return invalid
                        return super().call(role, payload, temperature)
                result = Engine(InvalidSynthesis(), DemoTools(), Config(max_steps=1)).run("test")
                self.assertEqual(result["answer"], "Insufficient evidence")
                self.assertFalse(result["memory"])
                self.assertTrue(any(e["event"] == "synthesis_error" for e in result["events"]))
                self.assertEqual(score(result["answer"], "28"), {"em": 0.0, "f1": 0.0})

    def test_failed_extraction_can_retry_same_action(self):
        class BadExtractionOnce(RecordingBackend):
            failed_once = False
            def call(self, role, payload, temperature):
                if role == "extractor" and not self.failed_once:
                    self.failed_once = True
                    return {"new_findings": [{"fact": "invented", "source": "unobserved"}]}
                return super().call(role, payload, temperature)
        backend = BadExtractionOnce()
        result = Engine(backend, DemoTools()).run("test")
        self.assertEqual(result["answer"], "28")
        self.assertTrue(any(e["event"] == "schema_error" for e in result["events"]))
        self.assertFalse(any(e["event"] == "duplicate_action" for e in result["events"]))
        birth_calls = [p for r, p, _ in backend.calls if r == "executor" and p["node"]["id"] == "birth"]
        self.assertEqual(birth_calls[1]["completed_actions"], [])

    def test_oversized_partial_does_not_abort_or_propagate(self):
        class OversizedPartial(RecordingBackend):
            def call(self, role, payload, temperature):
                if role == "forced_extractor":
                    return {"result": "x" * 2000, "evidence": [], "confidence": 0}
                return super().call(role, payload, temperature)
        backend = OversizedPartial()
        result = Engine(backend, DemoTools(), Config(max_steps=1)).run("test")
        self.assertEqual(result["answer"], "Insufficient evidence")
        self.assertFalse(result["memory"])
        self.assertFalse(result["partials"])
        self.assertTrue(any(e["event"] == "partial_error" for e in result["events"]))
        self.assertTrue(any(e["event"] == "blocked" for e in result["events"]))

    def test_repeated_successful_action_is_not_reexecuted(self):
        class RepeatOnce(RecordingBackend):
            def call(self, role, payload, temperature):
                if role == "executor" and payload["node"]["id"] == "birth" and payload["step"] == 1:
                    self.asserted_actions = payload["completed_actions"]
                    return {"action": "search", "arguments": {"query": "Ada Lovelace birth year"}}
                return super().call(role, payload, temperature)
        backend = RepeatOnce()
        result = Engine(backend, DemoTools()).run("test")
        self.assertTrue(backend.asserted_actions)
        self.assertEqual(result["answer"], "28")
        self.assertEqual(sum(e["event"] == "duplicate_action" for e in result["events"]), 1)
        self.assertEqual(sum(e["event"] == "tool" for e in result["events"]), 2)

    def test_demo_end_to_end(self):
        backend = RecordingBackend()
        result = Engine(backend, DemoTools()).run("test")
        self.assertEqual(result["answer"], "28")
        self.assertEqual(set(result["memory"]), {"birth", "age"})
        self.assertFalse(result["partials"])
        calls = [p for r, p, _ in backend.calls if r == "executor" and p["node"]["id"] == "age"]
        self.assertEqual(calls[0]["parents"]["birth"]["result"], "1815")
        self.assertNotIn("history", calls[0])
        for capsule in result["memory"].values():
            self.assertEqual(capsule["status"], Verdict.PASS)
            self.assertLessEqual(len(json.dumps(capsule, ensure_ascii=False).encode()), 512)

    def test_rejected_and_uncertain_retry_locally(self):
        backend = RecordingBackend(["Rejected", "Uncertain", "Pass", "Pass"])
        result = Engine(backend, DemoTools()).run("test")
        self.assertEqual(result["answer"], "28")
        states = [p["state"] for r, p, _ in backend.calls if r == "executor"]
        self.assertTrue(any(s["negative_constraints"] for s in states))
        self.assertTrue(any(s["feedback"] for s in states))
        self.assertEqual(sum(e["event"] == "tool" for e in result["events"]), 2)

    def test_timeout_never_propagates(self):
        result = Engine(DemoBackend(), DemoTools(), Config(max_steps=1)).run("test")
        self.assertFalse(result["memory"])
        self.assertEqual(result["partials"]["birth"]["status"], Verdict.UNCERTAIN)
        self.assertTrue(any(e["event"] == "blocked" and e["node"] == "age" for e in result["events"]))
        self.assertEqual(result["answer"], "Insufficient evidence")

    def test_rejection_budget(self):
        result = Engine(RecordingBackend(["Rejected"] * 8), DemoTools()).run("test")
        self.assertFalse(result["memory"])
        self.assertIn("birth", result["partials"])

    def test_graph_validation(self):
        for nodes in [[], [Node("a", "x"), Node("a", "y")],
                      [Node("a", "x", ("missing",))],
                      [Node("a", "x", ("b",)), Node("b", "y", ("a",))]]:
            with self.assertRaises(ValueError):
                Plan(nodes).ordered()
        self.assertEqual([n.id for n in Plan([Node("b", "x", ("a",)), Node("a", "y")]).ordered()], ["a", "b"])

    def test_candidate_validation(self):
        engine = Engine(DemoBackend(), DemoTools())
        for data in [{"result": "x", "confidence": float("nan")},
                     {"result": "x", "confidence": 2},
                     {"result": "x", "evidence": ["no source"]},
                     {"result": "x" * 1000}]:
            with self.assertRaises(ValueError):
                engine._candidate(Node("n", "task"), data)

    def test_arithmetic_restrictions(self):
        self.assertEqual(arithmetic("1843 - 1815"), "28")
        for expression in ["__import__('os').getcwd()", "2**999999", "[1]*100000", "open('/tmp/x','w')"]:
            with self.assertRaises(ValueError):
                arithmetic(expression)

    def test_metrics(self):
        self.assertEqual(score("The Nile!", ["Nile", "Amazon"]), {"em": 1.0, "f1": 1.0})
        self.assertEqual(score("wrong", "right")["em"], 0)
        self.assertEqual(score("a b c", "b d")["f1"], 0.5)

    def test_no_verification_ablation(self):
        backend = RecordingBackend()
        result = Engine(backend, DemoTools(), Config(verification=False)).run("test")
        self.assertEqual(result["answer"], "28")
        self.assertFalse(any(r == "verifier" for r, _, _ in backend.calls))

    def test_http_model_contract(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                data = json.dumps({"choices": [{"message": {"content": '{"answer":"ok"}'}}],
                                   "usage": {"total_tokens": 7}}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backend = OpenAIBackend(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="fixture")
            self.assertEqual(backend.call("synthesizer", {"query": "x"}, 0), {"answer": "ok"})
            self.assertEqual(captured[0]["top_p"], 1.0)
            self.assertEqual(backend.usage[0]["total_tokens"], 7)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

class EvaluationCLITests(unittest.TestCase):
    def test_invalid_final_answer_still_writes_scored_record(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from scope.cli import main
        class EmptyAnswerBackend(DemoBackend):
            model = "offline-fixture"
            usage = []
            def call(self, role, payload, temperature):
                if role == "synthesizer":
                    return {"answer": ""}
                return super().call(role, payload, temperature)
        with tempfile.TemporaryDirectory() as tmp:
            dataset, output = Path(tmp) / "input.jsonl", Path(tmp) / "result.jsonl"
            dataset.write_text(json.dumps({"id": "one", "question": "test", "answer": "28"}) + "\n")
            with patch("scope.cli.OpenAIBackend", EmptyAnswerBackend), patch("scope.cli.WebTools", lambda **kwargs: DemoTools()), patch(
                "sys.argv", ["scope", "evaluate", "--input", str(dataset), "--output", str(output), "--runs", "1", "--max-steps", "1"]):
                main()
            record = json.loads(output.read_text())
            summary = json.loads(output.with_suffix(".summary.json").read_text())
            self.assertEqual(record["result"]["answer"], "Insufficient evidence")
            self.assertEqual(summary["completed"], 1)
            self.assertEqual(summary["em"], 0)
            self.assertEqual(summary["f1"], 0)

    def test_three_run_evaluation(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from scope.cli import main
        class EvaluationBackend(DemoBackend):
            model = "offline-fixture"
            usage = []
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "input.jsonl"
            output = Path(tmp) / "results.jsonl"
            dataset.write_text(json.dumps({"id": "test", "question": "test", "answer": "28"}) + "\n")
            with patch("scope.cli.OpenAIBackend", EvaluationBackend), patch("scope.cli.WebTools", lambda **kwargs: DemoTools()), patch(
                "sys.argv", ["scope", "evaluate", "--input", str(dataset), "--output", str(output)]):
                main()
            summary = json.loads(output.with_suffix(".summary.json").read_text())
            self.assertEqual(summary["completed"], 3)
            self.assertEqual(summary["em"], 1.0)
            self.assertEqual(len(output.read_text().splitlines()), 3)
