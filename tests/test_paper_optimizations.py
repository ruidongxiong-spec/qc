"""Paper invariants and context/schema regressions; fixtures are not accuracy tests."""
import io
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from scope.backends import DemoBackend, OpenAIBackend, PROMPTS
from scope.benchmark import run_matrix
from scope.context import observation_chunks
from scope.core import Capsule, Config, Engine, Verdict
from scope.errors import ModelOutputError
from scope.structured import schema_for
from scope.token_budget import TokenCounter, prepare
from scope.tools import DemoTools


def fixture_tokenizer(path):
    from tokenizers import Tokenizer, models, pre_tokenizers
    tokenizer = Tokenizer(models.WordLevel({'[UNK]': 0, 'fact': 1}, unk_token='[UNK]'))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.enable_truncation(max_length=2)
    tokenizer.enable_padding(length=32)
    tokenizer.save(str(path))
    return path


@unittest.skipUnless(importlib.util.find_spec('tokenizers'), 'Install token-budget extra')
class TokenBudgetTests(unittest.TestCase):
    def test_complete_count_ignores_artifact_truncation_and_padding(self):
        with tempfile.TemporaryDirectory() as tmp:
            counter = TokenCounter(fixture_tokenizer(Path(tmp) / 'tokenizer.json'))
            self.assertEqual(counter.count('fact ' * 600), 600)
            self.assertEqual(counter.count('fact'), 1)

    def test_tokens_not_bytes_and_still_enforces_full_capsule(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture_tokenizer(Path(tmp) / 'tokenizer.json')
            candidate = Capsule('x', 'fact ' * 180, [], .5, 'checked', Verdict.PASS)
            engine = Engine(DemoBackend(), DemoTools(), Config(tokenizer_path=str(path)))
            engine._check_budget(candidate)
            self.assertEqual(engine.capsule_budget['unit'], 'tokens')
            self.assertEqual(len(engine.capsule_budget['tokenizer_sha256']), 64)
            with self.assertRaisesRegex(ValueError, 'utf8_bytes_legacy'):
                Engine(DemoBackend(), DemoTools())._check_budget(candidate)
            candidate.result = 'fact ' * 600
            with self.assertRaisesRegex(ValueError, 'tokens'):
                engine._check_budget(candidate)

    def test_download_is_revision_pinned_cache_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = fixture_tokenizer(Path(tmp) / 'fixture.json').read_bytes()
            destination = Path(tmp) / 'downloaded.json'
            revision = 'a' * 40
            with patch('scope.token_budget.urllib.request.urlopen', side_effect=[
                    io.BytesIO(json.dumps({'sha': revision}).encode()), io.BytesIO(data)]) as request:
                info = prepare('Test/Model', destination)
                self.assertIn('/resolve/' + revision + '/', request.call_args_list[1].args[0])
            with patch('scope.token_budget.urllib.request.urlopen', side_effect=AssertionError('Cache should be offline')):
                self.assertEqual(prepare('Test/Model', destination), info)
                with self.assertRaisesRegex(ValueError, 'provenance'):
                    prepare('Other/Model', destination)
                destination.write_bytes(data + b' ')
                with self.assertRaisesRegex(ValueError, 'provenance'):
                    prepare('Test/Model', destination)

    def test_context_overflow_is_explicit_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture_tokenizer(Path(tmp) / 'tokenizer.json')
            with patch.dict('os.environ', {'SCOPE_MODEL_API': 'ollama', 'SCOPE_TOKENIZER_PATH': str(path),
                                           'SCOPE_OLLAMA_CONTEXT': '2048'}):
                backend = OpenAIBackend(base_url='http://127.0.0.1:9999/v1', model='fixture')
            with patch('scope.backends.urllib.request.urlopen') as request:
                with self.assertRaisesRegex(ModelOutputError, 'context reserve'):
                    backend.call('extractor', {'observation': 'fact ' * 3000}, 0)
                request.assert_not_called()


class ContextTests(unittest.TestCase):
    def test_resume_rejects_changed_model_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = [{'name': 'fixture'}]
            rows = [{'id': 'one', 'question': 'test', 'answer': '28'}]
            with patch.dict('os.environ', {'SCOPE_OLLAMA_CONTEXT': '8192'}):
                run_matrix(models, rows, tmp, runs=1, demo=True)
            with patch.dict('os.environ', {'SCOPE_OLLAMA_CONTEXT': '4096'}):
                with self.assertRaisesRegex(ValueError, 'same configuration'):
                    run_matrix(models, rows, tmp, runs=1, demo=True, resume=True)

    def test_all_observation_text_preserved_in_bounded_chunks(self):
        rows = [{'source': 'a', 'text': '0123456789' * 900 + 'TAIL', 'title': 'A'},
                {'source': 'b', 'text': 'second'}, {'source': 'c', 'text': ''}]
        chunks = [json.loads(c)['results'] for c in observation_chunks({'results': rows}, 4000)]
        for chunk in chunks:
            self.assertLessEqual(sum(len(r['text']) for r in chunk), 4000)
        for row in rows:
            pieces = [r for chunk in chunks for r in chunk if r['source'] == row['source']]
            reconstructed = [''] * len(row['text'])
            for piece in pieces:
                start = piece['offset']
                self.assertEqual(piece['text'], row['text'][start:start + len(piece['text'])])
                reconstructed[start:start + len(piece['text'])] = piece['text']
            self.assertEqual(''.join(reconstructed), row['text'])
        # Facts crossing a boundary appear in full in the next chunk.
        boundary = 'x' * 3990 + 'Orion belongs to Group Z.' + 'x' * 40
        self.assertTrue(any('Orion belongs to Group Z.' in r['text']
                            for c in observation_chunks({'results': [{'source': 'a', 'text': boundary}]}, 4000)
                            for r in json.loads(c)['results']))

    def test_schema_scopes_sources_and_finish_action(self):
        schema = schema_for('extractor', {'allowed_sources': ['observed']})
        self.assertEqual(schema['properties']['new_findings']['items']['properties']['source']['enum'], ['observed'])
        self.assertFalse(schema['additionalProperties'])
        self.assertIn('new_findings', schema['required'])
        self.assertEqual(schema_for('executor', {'allowed_actions': ['finish']})['properties']['action']['enum'], ['finish'])

    def test_nullable_unused_arguments_do_not_evade_duplicate_detection(self):
        engine = Engine(DemoBackend(), DemoTools())
        a = engine._action({'action': 'search', 'arguments': {'query': 'entity', 'url': None, 'expression': None}})
        b = engine._action({'action': 'search', 'arguments': {'query': 'entity'}})
        self.assertEqual(a, b)

    def test_bad_chunk_does_not_hide_later_fact_or_leak_raw_history(self):
        calls = []
        class Tools:
            count = 0
            def execute(self, name, arguments):
                self.count += 1
                return json.dumps({'results': [{'source': 'source', 'text': 'x' * 512 + 'Orion belongs to Group Z.'}]})
        class Backend(DemoBackend):
            def call(self, role, payload, temperature):
                calls.append((role, payload))
                if role == 'executor':
                    if payload['state']['new_findings']:
                        return {'action': 'finish', 'result': 'Group Z', 'evidence': payload['state']['new_findings'], 'confidence': .9}
                    return {'action': 'visit', 'arguments': {'url': 'https://source.test'}}
                if role == 'extractor':
                    if payload['chunk']['index'] == 1:
                        return {'@type': 'CreativeWork'}
                    text = json.loads(payload['observation'])['results'][0]['text']
                    return {'new_findings': [{'source': 'source', 'fact': 'Orion belongs to Group Z.'}] if 'Group Z' in text else [],
                            'missing_info': [], 'new_leads': ['https://source.test']}
                if role == 'verifier':
                    return {'verdict': 'Pass', 'reason': 'Supported by source'}
                if role == 'synthesizer':
                    return {'answer': payload['verified_memory']['task']['result']}
                return super().call(role, payload, temperature)
        tools = Tools()
        result = Engine(Backend(), tools, Config(dag=False, observation_chunk_chars=256)).run('Which group?')
        self.assertEqual(result['answer'], 'Group Z')
        self.assertEqual(tools.count, 1)
        self.assertTrue(any(e.get('chunk') == 1 and e['event'] == 'schema_error' for e in result['events']))
        executors = [p for r, p in calls if r == 'executor']
        self.assertNotIn('history', executors[-1])
        self.assertEqual(executors[-1]['state']['new_leads'], [])
        self.assertNotIn('x' * 256, json.dumps(executors[-1]))
        self.assertEqual(len([p for r, p in calls if r == 'extractor']), 4)

    def test_repeated_action_requests_finish_but_cannot_skip_verifier(self):
        for verdict in ('Pass', 'Rejected'):
            with self.subTest(verdict=verdict):
                class Backend(DemoBackend):
                    finish_requests = 0
                    def call(self, role, payload, temperature):
                        if role == 'executor':
                            if payload['finish_requested']:
                                self.finish_requests += 1
                                return {'action': 'finish', 'result': '1815', 'evidence': payload['state']['new_findings'], 'confidence': .9}
                            return {'action': 'search', 'arguments': {'query': 'Ada Lovelace birth year'}}
                        if role == 'verifier':
                            return {'verdict': verdict, 'reason': 'Fixture decision'}
                        if role == 'synthesizer':
                            return {'answer': '1815'}
                        return super().call(role, payload, temperature)
                backend = Backend()
                result = Engine(backend, DemoTools(), Config(dag=False, max_steps=5)).run('birth year?')
                self.assertGreater(backend.finish_requests, 0)
                self.assertEqual(bool(result['memory']), verdict == 'Pass')
                self.assertLessEqual(len([e for e in result['events'] if e['event'] == 'tool']), 1)
                if verdict == 'Rejected':
                    self.assertEqual(result['outcome'], 'abstained')


class NativeEndpointTests(unittest.TestCase):
    def test_ollama_schema_context_and_usage_over_real_http(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body))
                raw = json.dumps({'message': {'content': '{"answer":"fixture"}'}, 'done_reason': 'stop',
                                  'prompt_eval_count': 50, 'eval_count': 5}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict('os.environ', {'SCOPE_MODEL_API': 'ollama', 'SCOPE_RESPONSE_FORMAT': 'json_schema',
                                           'SCOPE_OLLAMA_CONTEXT': '8192', 'SCOPE_TOKENIZER_PATH': '', 'NO_PROXY': '127.0.0.1'}):
                backend = OpenAIBackend(base_url=f'http://127.0.0.1:{server.server_port}/v1', model='fixture')
                self.assertEqual(backend.call('synthesizer', {}, 0), {'answer': 'fixture'})
            path, body = requests[0]
            self.assertEqual(path, '/api/chat')
            self.assertEqual(body['format'], schema_for('synthesizer', {}))
            self.assertEqual(body['options']['num_ctx'], 8192)
            self.assertEqual(body['options']['num_predict'], 768)
            self.assertEqual(backend.usage[0]['total_tokens'], 55)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_openai_strict_schema_remains_separate_from_native_protocol(self):
        with patch.dict('os.environ', {'SCOPE_MODEL_API': 'openai', 'SCOPE_RESPONSE_FORMAT': 'json_schema', 'SCOPE_TOKENIZER_PATH': ''}):
            backend = OpenAIBackend(base_url='http://127.0.0.1:9999/v1', model='fixture')
        with patch('scope.backends.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"{\\"verdict\\":\\"Pass\\",\\"reason\\":\\"OK\\"}"}}]}'
            backend.call('verifier', {}, .7)
            req = request.call_args.args[0]
            body = json.loads(req.data)
            self.assertTrue(req.full_url.endswith('/v1/chat/completions'))
            self.assertTrue(body['response_format']['json_schema']['strict'])
            self.assertEqual(body['max_tokens'], 128)
