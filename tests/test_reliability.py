"""Fault injection at real runner boundaries; no external credentials/network."""
import io
import json
import tempfile
import threading
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from scope.backends import DemoBackend, OpenAIBackend, PROMPTS
from scope.cli import main
from scope.core import Config, Engine
from scope.errors import ModelOutputError, ModelTransportError
from scope.progress import log, stage
from scope.retrieval import Retrieval
from scope.tools import DemoTools


class ScriptedBackend(DemoBackend):
    def __init__(self, faults=None):
        self.faults = faults or {}
        self.calls = []
        self.counts = Counter()

    def call(self, role, payload, temperature):
        self.calls.append((role, json.loads(json.dumps(payload))))
        index = self.counts[role]
        self.counts[role] += 1
        faults = self.faults.get(role, [])
        if index < len(faults):
            value = faults[index]
            if isinstance(value, Exception):
                raise value
            return value
        return super().call(role, payload, temperature)


class ContractRecoveryTests(unittest.TestCase):
    def test_unknown_dependency_is_repaired_before_execution(self):
        bad = {"nodes": [{"id": "n1", "description": "test", "dependencies": ["absent"]}]}
        backend = ScriptedBackend({"planner": [bad]})
        result = Engine(backend, DemoTools()).run("test")
        self.assertEqual(result["answer"], "28")
        self.assertEqual(result["status"], "completed")
        repair = backend.calls[1][1]["repair"]
        self.assertIn("absent", repair["error"])
        self.assertEqual(backend.counts["planner"], 2)
        self.assertTrue(any(e["role"] == "planner" for e in result["events"] if e["event"] == "model_output_error"))

    def test_invalid_plans_are_bounded_errors_not_fake_successes(self):
        cases = [None, [], {}, {"nodes": []}, {"nodes": "n1"}, {"nodes": [None]},
                 {"nodes": [{"id": "x", "description": "x", "dependencies": "x"}]},
                 {"nodes": [{"id": "x", "description": "x", "dependencies": ["x"]}]},
                 {"nodes": [{"id": "x", "description": "x"}] * 2},
                 {"nodes": [{"id": "x", "description": "x", "dependencies": ["y"]},
                            {"id": "y", "description": "y", "dependencies": ["x"]}]}]
        for bad in cases:
            with self.subTest(bad=bad):
                backend = ScriptedBackend({"planner": [bad, bad]})
                engine = Engine(backend, DemoTools())
                with self.assertRaises(ModelOutputError):
                    engine.run("test")
                self.assertEqual(backend.counts["planner"], 2)
                self.assertEqual(backend.counts["executor"], 0)
                self.assertEqual(engine.last_result["status"], "error")
                self.assertEqual(engine.last_result["error"]["phase"], "planner")
                self.assertFalse(engine.last_result["plan"])

    def test_malformed_action_uses_step_budget(self):
        cases = [None, [], {"action": []}, {"action": "search", "arguments": None},
                 {"action": "visit", "arguments": {}}, {"action": "search", "arguments": {"query": 7}}]
        for bad in cases:
            with self.subTest(bad=bad):
                backend = ScriptedBackend({"executor": [bad]})
                result = Engine(backend, DemoTools()).run("test")
                self.assertEqual(result["answer"], "28")
                self.assertTrue(any(e["event"] == "schema_error" for e in result["events"]))

    def test_extraction_repair_reuses_observation_not_network(self):
        class CountTools(DemoTools):
            count = 0
            def execute(self, name, arguments):
                self.count += 1
                return super().execute(name, arguments)
        backend = ScriptedBackend({"extractor": [{"new_findings": [{"fact": "x", "source": "fabricated"}]}]})
        tools = CountTools()
        result = Engine(backend, tools).run("test")
        self.assertEqual(result["answer"], "28")
        self.assertEqual(tools.count, 2)
        self.assertEqual(backend.counts["extractor"], 3)
        repairs = [p for r, p in backend.calls if r == "extractor" and "repair" in p]
        self.assertIn("fixture", repairs[0]["allowed_sources"])

    def test_verifier_and_synthesis_contracts_can_be_corrected(self):
        backend = ScriptedBackend({"verifier": [{"verdict": "yes", "reason": None}],
                                   "synthesizer": [{"answer": ""}]})
        result = Engine(backend, DemoTools()).run("test")
        self.assertEqual(result["outcome"], "answered")
        self.assertEqual(result["answer"], "28")
        self.assertEqual(backend.counts["synthesizer"], 2)

    def test_transport_failure_is_not_retried_as_output_repair(self):
        backend = ScriptedBackend({"planner": [ModelTransportError("HTTP 503")]})
        engine = Engine(backend, DemoTools())
        with self.assertRaises(ModelTransportError):
            engine.run("test")
        self.assertEqual(backend.counts["planner"], 1)
        self.assertEqual(engine.last_result["error"]["phase"], "planner")

    def test_inflight_failure_preserves_verified_progress(self):
        class FailsOnSecondNode(DemoBackend):
            def call(self, role, payload, temperature):
                if role == "executor" and payload["node"]["id"] == "age":
                    raise ModelTransportError("connection lost")
                return super().call(role, payload, temperature)
        engine = Engine(FailsOnSecondNode(), DemoTools())
        with self.assertRaises(ModelTransportError):
            engine.run("test")
        self.assertEqual(engine.last_result["memory"]["birth"]["result"], "1815")
        self.assertEqual(engine.last_result["status"], "error")

    def test_unverified_claims_cannot_become_answer(self):
        backend = ScriptedBackend({"synthesizer": [{"answer": "invented answer"}]})
        result = Engine(backend, DemoTools(), Config(max_steps=1)).run("test")
        self.assertEqual(result["outcome"], "abstained")
        self.assertEqual(result["answer"], "Insufficient evidence")


class EndpointContractTests(unittest.TestCase):
    def test_response_envelope_errors_are_typed(self):
        malformed = [b'not-json', b'null', b'[]', b'{}', b'{"choices":[]}',
                     b'{"choices":[{"message":{"content":null}}]}',
                     b'{"choices":[{"message":{"content":"[]"}}]}',
                     b'{"choices":[{"message":{"content":"{}"},"finish_reason":"length"}]}']
        backend = OpenAIBackend(base_url="http://127.0.0.1:9999/v1", model="fixture")
        for body in malformed:
            with self.subTest(body=body), patch("scope.backends.urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = body
                with self.assertRaises(ModelOutputError):
                    backend.call("planner", {}, 0)

    def test_null_usage_is_accepted_and_timeout_is_transport_error(self):
        backend = OpenAIBackend(base_url="http://127.0.0.1:9999/v1", model="fixture")
        with patch("scope.backends.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
                "choices": [{"message": {"content": '{"answer":"ok"}'}}], "usage": None}).encode()
            self.assertEqual(backend.call("synthesizer", {}, 0), {"answer": "ok"})
        with patch("scope.backends.urllib.request.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(ModelTransportError):
                backend.call("synthesizer", {}, 0)


class PersistenceTests(unittest.TestCase):
    def test_http_model_plan_repair_through_cli_to_saved_score(self):
        requests = []
        fixture = DemoBackend()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                role = next(role for role, prompt in PROMPTS.items() if body['messages'][0]['content'].startswith(prompt))
                payload = json.loads(body['messages'][1]['content'])
                requests.append((role, payload))
                if len(requests) == 1:
                    output = {"nodes": [{"id": "n1", "description": "x", "dependencies": ["missing"]}]}
                else:
                    output = fixture.call(role, payload, body['temperature'])
                encoded = json.dumps({'choices': [{'message': {'content': json.dumps(output)}}], 'usage': None}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(encoded)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source, output = Path(tmp) / 'input.jsonl', Path(tmp) / 'output.jsonl'
                source.write_text(json.dumps({'id': 'one', 'question': 'test', 'answer': '28'}) + '\n')
                with patch.dict('os.environ', {'SCOPE_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                                              'SCOPE_MODEL': 'fixture', 'SCOPE_API_KEY': '', 'NO_PROXY': '127.0.0.1'}), patch(
                    'scope.cli.WebTools', lambda **kw: DemoTools()), patch('sys.argv', [
                        'scope', 'evaluate', '--input', str(source), '--output', str(output), '--runs', '1']), redirect_stdout(io.StringIO()):
                    main()
                record = json.loads(output.read_text())
                self.assertEqual(record['status'], 'completed')
                self.assertEqual(record['result']['answer'], '28')
                self.assertEqual(record['metrics']['em'], 1)
                self.assertIn('repair', requests[1][1])
                self.assertTrue(output.with_suffix('.summary.json').exists())
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_failed_sample_is_saved_and_next_sample_runs(self):
        class Backend(DemoBackend):
            model = "fixture"
            usage = []
            def call(self, role, payload, temperature):
                if role == "planner" and payload["query"] == "broken":
                    return {"nodes": [{"id": "n1", "description": "x", "dependencies": ["missing"]}]}
                return super().call(role, payload, temperature)
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "input.jsonl", Path(tmp) / "output.jsonl"
            source.write_text('\n'.join(json.dumps({"id": str(i), "question": q, "answer": "28"})
                                        for i, q in enumerate(["broken", "valid"])))
            with patch("scope.cli.OpenAIBackend", Backend), patch("scope.cli.WebTools", lambda **kw: DemoTools()), patch(
                "sys.argv", ["scope", "evaluate", "--input", str(source), "--output", str(output), "--runs", "1"]), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as exit:
                    main()
            self.assertEqual(exit.exception.code, 1)
            records = [json.loads(s) for s in output.read_text().splitlines()]
            self.assertEqual([r["status"] for r in records], ["error", "completed"])
            self.assertEqual(records[0]["error"]["phase"], "planner")
            self.assertIsNone(records[0]["metrics"])
            summary = json.loads(output.with_suffix('.summary.json').read_text())
            self.assertEqual((summary["attempted"], summary["completed"], summary["failed"]), (2, 1, 1))
            self.assertEqual(summary["em"], 0.5)

    def test_corrupted_cache_is_refetched_and_failed_calls_are_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Retrieval(search="wikipedia", cache_dir=tmp)
            with patch.object(adapter, "_search", return_value=[{"source": "https://example.com", "text": "x"}]) as search:
                adapter.execute("search", {"query": "test"})
                next(Path(tmp).glob('*.json')).write_text('{broken')
                adapter.execute("search", {"query": "test"})
                self.assertEqual(search.call_count, 2)
                self.assertFalse(adapter.audit[-1]["cache_hit"])
            with patch.object(adapter, "_search", side_effect=ValueError("failed")):
                with self.assertRaises(ValueError):
                    adapter.execute("search", {"query": "other"})
            self.assertEqual(adapter.audit[-1]["status"], "error")

    def test_log_file_and_heartbeat_are_flushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.log'
            notified = threading.Event()
            import scope.progress as progress
            original = progress.log
            def capture(message):
                original(message)
                if message.startswith('WAIT'):
                    notified.set()
            with patch.dict('os.environ', {'SCOPE_VERBOSE': '', 'SCOPE_LOG_FILE': str(path), 'SCOPE_API_KEY': 'secret-key'}), patch('scope.progress.log', capture):
                with stage('slow request', heartbeat_seconds=0.01):
                    log('secret-key')
                    self.assertTrue(notified.wait(1))
                text = path.read_text()
            self.assertIn('WAIT slow request', text)
            self.assertIn('DONE slow request', text)
            self.assertNotIn('secret-key', text)
