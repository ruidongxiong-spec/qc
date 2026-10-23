import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scope.backends import DemoBackend
from scope.benchmark import run_matrix, summarize
from scope.core import Engine
from scope.report import render
from scope.retrieval import Retrieval, ToolError
from scope.tools import DemoTools, public_url


class RetrievalTests(unittest.TestCase):
    def test_jina_does_not_preflight_local_dns(self):
        from scope.retrieval import fetch
        with patch("scope.tools.socket.getaddrinfo", side_effect=AssertionError("Local DNS used")), patch("scope.retrieval.urllib.request.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b"Evidence"
            response.headers.get_content_charset.return_value = "utf-8"
            response.headers.get_content_type.return_value = "text/plain"
            self.assertEqual(fetch("https://r.jina.ai/https://example.com", jina_endpoint=True)[0], "Evidence")
            with patch("scope.retrieval.fetch", return_value=("Evidence", "text/plain")):
                self.assertTrue(Retrieval(reader="jina")._visit("https://example.com"))

    def test_direct_reader_still_checks_dns(self):
        with patch("scope.tools.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("2001::1", 443))]):
            with self.assertRaises(ValueError):
                Retrieval(reader="direct")._visit("https://example.com")

    def test_jina_encodes_model_url_without_double_encoding(self):
        adapter = Retrieval(reader="jina")
        url = "https://example.com/encyclopedia articles/café%20page?q=a b&x=1#section"
        expected = "https://example.com/encyclopedia%20articles/caf%C3%A9%20page?q=a%20b&x=1"
        with patch("scope.retrieval.public_url"), patch("scope.retrieval.fetch", return_value=("Evidence", "text/plain")) as fetch:
            result = adapter._visit(url)
        self.assertEqual(result[0]["source"], expected)
        self.assertEqual(fetch.call_args.args[0], "https://r.jina.ai/" + expected)

    def test_visit_rejects_control_characters_before_network(self):
        with patch("scope.retrieval.public_url") as validate:
            with self.assertRaises(ToolError):
                Retrieval(reader="jina")._visit("https://example.com/a\nheader")
        validate.assert_not_called()

    def test_http_protocol_failure_becomes_recoverable_tool_error(self):
        import http.client
        from scope.retrieval import fetch
        with patch("scope.retrieval.public_url"), patch("scope.retrieval.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = http.client.InvalidURL("bad request target")
            with self.assertRaises(ToolError):
                fetch("https://example.com")

    def test_cache_prevents_repeated_provider_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Retrieval(search="wikipedia", cache_dir=tmp)
            rows = [{"source": "https://example.com", "title": "Example", "text": "Evidence"}]
            with patch.object(adapter, "_search", return_value=rows) as search:
                first = adapter.execute("search", {"query": "test"})
                second = adapter.execute("search", {"query": "test"})
            self.assertEqual(first, second)
            self.assertEqual(search.call_count, 1)
            self.assertTrue(adapter.audit[-1]["cache_hit"])
            self.assertIn("retrieved_at", json.loads(first))

    def test_provider_separates_cache_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Retrieval(search="wikipedia", cache_dir=tmp)
            b = Retrieval(search="serper", cache_dir=tmp)
            rows = [{"source": "https://example.com", "text": "x"}]
            with patch.object(a, "_search", return_value=rows), patch.object(b, "_search", return_value=rows):
                a.execute("search", {"query": "test"})
                b.execute("search", {"query": "test"})
            self.assertNotEqual(a.audit[0]["cache_key"], b.audit[0]["cache_key"])

    @unittest.skipUnless(importlib.util.find_spec("trafilatura"), "Install free-web extra for HTML extraction")
    def test_html_extraction_with_real_library(self):
        article = '<html><body><article><h1>Ada Lovelace</h1>' + '<p>Ada Lovelace was born in 1815. She wrote notes on the Analytical Engine in 1843.</p>' * 12 + '</article></body></html>'
        reader = Retrieval(reader="direct")
        with patch("scope.retrieval.public_url", side_effect=lambda u: u), patch("scope.retrieval.fetch", return_value=(article, "text/html")):
            results = reader._visit("https://example.com/article")
        self.assertIn("1815", results[0]["text"])
        self.assertNotIn("<article>", results[0]["text"])

    def test_private_destinations_rejected(self):
        for url in ["file:///etc/passwd", "http://example.com", "https://127.0.0.1", "https://user:password@example.com"]:
            with self.assertRaises(ValueError):
                public_url(url)

    def test_searx_requires_explicit_endpoint(self):
        with patch.dict("os.environ", {"SEARXNG_BASE_URL": ""}):
            with self.assertRaises(ToolError):
                Retrieval(search="searxng")._search("test")


class GovernanceTests(unittest.TestCase):
    def test_fabricated_evidence_cannot_commit(self):
        class FakeEvidence(DemoBackend):
            def call(self, role, payload, temperature):
                result = super().call(role, payload, temperature)
                if role == "executor" and result["action"] == "finish":
                    result["evidence"] = [{"fact": "invented", "source": "https://invented.invalid"}]
                return result
        result = Engine(FakeEvidence(), DemoTools()).run("test")
        self.assertFalse(result["memory"])
        self.assertTrue(any(e.get("verdict") == "Rejected" for e in result["events"]))

    def test_malformed_action_is_repaired_within_budget(self):
        class BadThenGood(DemoBackend):
            calls = 0
            def call(self, role, payload, temperature):
                if role == "executor":
                    self.calls += 1
                    if self.calls == 1:
                        return {"action": "unknown"}
                return super().call(role, payload, temperature)
        result = Engine(BadThenGood(), DemoTools()).run("test")
        self.assertEqual(result["answer"], "28")
        self.assertTrue(any(e["event"] == "schema_error" for e in result["events"]))


class BenchmarkTests(unittest.TestCase):
    def test_resume_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = [{"name": "fixture-A"}, {"name": "fixture-B"}]
            rows = [{"id": "one", "question": "test", "answer": "28"}]
            report = run_matrix(models, rows, tmp, demo=True)
            self.assertEqual(report["recorded"], 6)
            self.assertTrue(all(m["succeeded"] == 3 for m in report["models"]))
            run_matrix(models, rows, tmp, demo=True, resume=True)
            self.assertEqual(len((Path(tmp) / "records.jsonl").read_text().splitlines()), 6)
            with self.assertRaises(ValueError):
                run_matrix(models, rows, tmp, runs=1, demo=True, resume=True)
            self.assertIn("非真实模型性能", render(report))

    def test_failure_is_not_silently_dropped(self):
        report = summarize([{"model_name": "m", "status": "error"}])[0]
        self.assertEqual(report["errors"], 1)
        self.assertEqual(report["em_all_attempts"], 0)
        self.assertIsNone(report["em_success_only"])

    def test_html_escapes_untrusted_content(self):
        result = Engine(DemoBackend(), DemoTools()).run("<script>alert(1)</script>")
        document = render(result)
        self.assertNotIn("<script>", document)
        self.assertIn("&lt;script&gt;", document)

class FailureIsolationTests(unittest.TestCase):
    def test_matrix_records_endpoint_error_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("scope.benchmark.OpenAIBackend", side_effect=RuntimeError("Endpoint unavailable")):
                report = run_matrix([{"name": "a", "base_url": "https://example.com/v1", "model": "a"},
                                     {"name": "b", "base_url": "https://example.com/v1", "model": "b"}],
                                    [{"id": "one", "question": "test", "answer": "28"}], tmp, runs=1)
            self.assertEqual(report["recorded"], 2)
            self.assertTrue(all(m["errors"] == 1 for m in report["models"]))
            self.assertTrue(all(m["em_all_attempts"] == 0 for m in report["models"]))

    def test_explicit_empty_model_key_never_uses_other_binding(self):
        from scope.backends import OpenAIBackend
        with patch.dict("os.environ", {"SCOPE_API_KEY": "unrelated-test-fixture"}):
            client = OpenAIBackend(base_url="https://example.com/v1", model="fixture", key="")
        self.assertEqual(client.key, "")
