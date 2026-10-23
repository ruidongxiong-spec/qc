"""OpenRouter wire/provenance tests with fixtures, never real credentials/billing."""
import io
import json
import unittest
from unittest.mock import patch

from scope.backends import DemoBackend, OpenAIBackend, PROMPTS
from scope.core import Engine
from scope.errors import ModelTransportError
from scope.openrouter import free_models, inspect_model, read_models
from scope.progress import redact
from scope.tools import DemoTools

MODEL = 'liquid/lfm-2.5-2.6b:free'
ENV = {'OPENROUTER_API_KEY': 'test-openrouter-secret', 'SCOPE_API_KEY': 'old-key',
       'SCOPE_MODEL_API': 'openai', 'SCOPE_RESPONSE_FORMAT': 'json_object', 'SCOPE_TOKENIZER_PATH': ''}


class OpenRouterTests(unittest.TestCase):
    def backend(self):
        return OpenAIBackend(base_url='https://openrouter.ai/api/v1', model=MODEL)

    def test_request_auth_model_free_routing_and_supported_options(self):
        with patch.dict('os.environ', ENV), patch('scope.backends.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps({
                'model': MODEL, 'provider': 'fixture-provider',
                'choices': [{'message': {'content': '{"answer":"fixture"}'}}],
                'usage': {'total_tokens': 20, 'cost': 0}}).encode()
            backend = self.backend()
            backend.call('synthesizer', {}, 0)
            req = request.call_args.args[0]
            body = json.loads(req.data)
            self.assertEqual(req.full_url, 'https://openrouter.ai/api/v1/chat/completions')
            self.assertEqual(req.get_header('Authorization'), 'Bearer test-openrouter-secret')
            self.assertEqual(body['model'], MODEL)
            self.assertEqual(body['provider'], {'require_parameters': True, 'max_price': {'prompt': 0, 'completion': 0}})
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            self.assertEqual(body['max_tokens'], 768)
            self.assertFalse(body['stream'])
            for key in ('options', 'format', 'top_p'):
                self.assertNotIn(key, body)
            self.assertIn('Required JSON structure', body['messages'][0]['content'])
            self.assertNotIn('test-openrouter-secret', json.dumps(body))
            self.assertEqual(backend.usage[0]['cost'], 0)
            self.assertEqual(backend.usage[0]['served_model'], MODEL)

    def test_requires_openrouter_key_and_never_reuses_other_provider_key(self):
        with patch.dict('os.environ', {**ENV, 'OPENROUTER_API_KEY': ''}):
            with self.assertRaisesRegex(ValueError, 'OPENROUTER_API_KEY'):
                self.backend()

    def test_diagnostics_redact_openrouter_key(self):
        with patch.dict('os.environ', ENV):
            self.assertNotIn(ENV['OPENROUTER_API_KEY'], redact('request failed ' + ENV['OPENROUTER_API_KEY']))

    def test_error_envelope_is_not_retried_as_bad_model_json(self):
        with patch.dict('os.environ', ENV), patch('scope.backends.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps({
                'error': {'code': 429, 'message': 'limited test-openrouter-secret'}}).encode()
            with self.assertRaises(ModelTransportError) as caught:
                self.backend().call('planner', {}, .7)
            self.assertNotIn(ENV['OPENROUTER_API_KEY'], str(caught.exception))
            self.assertIn('429', str(caught.exception))

    def test_full_engine_through_openrouter_response_envelopes(self):
        roles = []
        fixture = DemoBackend()
        def respond(req, **kw):
            body = json.loads(req.data)
            role = next(r for r, prompt in PROMPTS.items() if body['messages'][0]['content'].startswith(prompt))
            roles.append(role)
            payload = json.loads(body['messages'][1]['content'])
            output = fixture.call(role, payload, body['temperature'])
            return io.BytesIO(json.dumps({'model': MODEL, 'usage': {'total_tokens': 20, 'cost': 0},
                              'choices': [{'message': {'content': json.dumps(output)}, 'finish_reason': 'stop'}]}).encode())
        with patch.dict('os.environ', ENV), patch('scope.backends.urllib.request.urlopen', side_effect=respond):
            result = Engine(self.backend(), DemoTools()).run('fixture')
        self.assertEqual(result['answer'], '28')
        self.assertEqual(result['outcome'], 'answered')
        self.assertEqual(roles.count('verifier'), 2)

    def metadata(self, row):
        return io.BytesIO(json.dumps({'data': [row]}).encode())

    def test_metadata_uses_exact_model_and_advertised_tokenizer(self):
        row = {'id': MODEL, 'hugging_face_id': 'Fixture/Tokenizer', 'pricing': {'prompt': '0', 'completion': '0'},
               'context_length': 32000, 'supported_parameters': ['response_format']}
        with patch.dict('os.environ', ENV), patch('scope.openrouter.urllib.request.urlopen', return_value=self.metadata(row)) as request:
            info = inspect_model(MODEL)
        self.assertEqual(info['hugging_face_id'], 'Fixture/Tokenizer')
        self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Bearer test-openrouter-secret')
        self.assertNotIn('test-openrouter-secret', json.dumps(info))

    def test_unknown_model_or_nonfree_metadata_stops_before_generation(self):
        cases = [
            {'id': MODEL.removesuffix(':free'), 'pricing': {'prompt': '0', 'completion': '0'}},
            {'id': MODEL, 'pricing': {'prompt': '.01', 'completion': '0'}},
            {'id': MODEL, 'pricing': {'prompt': '0', 'completion': '0', 'request': '.01'}},
            {'id': MODEL, 'pricing': {'prompt': 'NaN', 'completion': '0'}},
            {'id': MODEL, 'pricing': {}},
        ]
        for row in cases:
            with self.subTest(row=row), patch.dict('os.environ', ENV), patch(
                    'scope.openrouter.urllib.request.urlopen', return_value=self.metadata(row)):
                with self.assertRaises(ValueError):
                    inspect_model(MODEL)

    def test_missing_tokenizer_is_not_replaced_by_qwen(self):
        row = {'id': MODEL, 'pricing': {'prompt': '0', 'completion': '0'}}
        with patch.dict('os.environ', ENV), patch('scope.openrouter.urllib.request.urlopen', return_value=self.metadata(row)):
            self.assertIsNone(inspect_model(MODEL)['hugging_face_id'])

    def test_discovery_needs_no_key_and_sends_no_empty_authorization(self):
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': ''}), patch(
                'scope.openrouter.urllib.request.urlopen', return_value=io.BytesIO(b'{"data":[]}')) as request:
            self.assertEqual(read_models(), [])
        self.assertIsNone(request.call_args.args[0].get_header('Authorization'))

    def test_alternatives_are_free_actual_catalog_entries_not_guessed_ids(self):
        rows = [
            {'id': 'liquid/fixture-free', 'pricing': {'prompt': '0.0', 'completion': '0'}},
            {'id': 'liquid/fixture-paid', 'pricing': {'prompt': '0.1', 'completion': '0'}},
            {'id': 'liquid/fixture-unknown'},
            {'id': 'liquid/fixture-nan', 'pricing': {'prompt': 'NaN', 'completion': '0'}},
            {'id': 'liquid/fixture-request-charge', 'pricing': {'prompt': '0', 'completion': '0', 'request': '1'}},
            {'id': 'other/fixture-free', 'pricing': {'prompt': '0', 'completion': '0'}},
        ]
        self.assertEqual([r['id'] for r in free_models(rows)], ['liquid/fixture-free'])
        with patch.dict('os.environ', ENV), patch('scope.openrouter.read_models', return_value=rows):
            with self.assertRaises(ValueError) as caught:
                inspect_model(MODEL)
        message = str(caught.exception)
        self.assertIn('liquid/fixture-free', message)
        self.assertNotIn('liquid/fixture-paid', message)
        self.assertIn('no model was substituted', message)
        self.assertIn('does not validate your API key', message)

    def test_invalid_catalog_is_not_reported_as_missing_model(self):
        with patch('scope.openrouter.urllib.request.urlopen', return_value=io.BytesIO(b'{"data":{}}')):
            with self.assertRaisesRegex(ValueError, 'Invalid OpenRouter model catalog'):
                read_models()
