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


if __name__ == "__main__":
    unittest.main()

class EvaluationCLITests(unittest.TestCase):
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
            with patch("scope.cli.OpenAIBackend", EvaluationBackend), patch("scope.cli.WebTools", DemoTools), patch(
                "sys.argv", ["scope", "evaluate", "--input", str(dataset), "--output", str(output)]):
                main()
            summary = json.loads(output.with_suffix(".summary.json").read_text())
            self.assertEqual(summary["completed"], 3)
            self.assertEqual(summary["em"], 1.0)
            self.assertEqual(len(output.read_text().splitlines()), 3)
