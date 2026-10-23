"""Regression for the user's length-stop followed by Ollama token-repeat 500."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from scope.backends import DemoBackend, OpenAIBackend
from scope.core import Engine
from scope.errors import ModelGenerationError, ModelOutputError, ModelTransportError
from scope.structured import schema_for
from scope.tools import DemoTools


class GenerationRecoveryTests(unittest.TestCase):
    def backend(self, api='ollama'):
        with patch.dict('os.environ', {'SCOPE_MODEL_API': api, 'SCOPE_RESPONSE_FORMAT': 'json_schema',
                                       'SCOPE_TOKENIZER_PATH': ''}):
            return OpenAIBackend(base_url='http://127.0.0.1:9999/v1', model='fixture', key='fixture-secret')

    def test_only_exact_ollama_repeat_abort_is_recoverable(self):
        for api, code, message, expected in [
            ('ollama', 500, 'prediction aborted, token repeat limit reached', ModelGenerationError),
            ('ollama', 500, 'runner out of memory', ModelTransportError),
            ('ollama', 401, 'prediction aborted, token repeat limit reached', ModelTransportError),
            ('openai', 500, 'prediction aborted, token repeat limit reached', ModelTransportError),
            ('ollama', 500, 'unrelated error with token repeat limit reached in debug text', ModelTransportError),
        ]:
            with self.subTest(api=api, code=code, message=message):
                error = urllib.error.HTTPError('http://127.0.0.1', code, 'error', {},
                                              io.BytesIO(json.dumps({'error': message}).encode()))
                with patch('scope.backends.urllib.request.urlopen', side_effect=error):
                    with self.assertRaises(expected):
                        self.backend(api).call('executor', {}, .7)

    def test_truncated_valid_json_is_rejected_with_bounded_redacted_head_tail(self):
        content = json.dumps({'answer': 'fixture-secret BEGIN ' + 'r' * 3000 + ' END fixture-secret'})
        envelope = {'message': {'content': content}, 'done_reason': 'length', 'eval_count': 768}
        with patch('scope.backends.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
            with self.assertRaises(ModelOutputError) as caught:
                self.backend().call('synthesizer', {}, 0)
        preview = caught.exception.response_preview
        self.assertLessEqual(len(preview), 1000)
        self.assertIn('BEGIN', preview)
        self.assertIn('END', preview)
        self.assertNotIn('fixture-secret', preview)

    def test_malformed_json_also_preserves_preview(self):
        with patch('scope.backends.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps({
                'message': {'content': '{"result":broken'}, 'done_reason': 'stop'}).encode()
            with self.assertRaises(ModelOutputError) as caught:
                self.backend().call('executor', {}, 0)
        self.assertIn('broken', caught.exception.response_preview)

    def test_length_then_repeat_error_recovers_within_steps_and_verifies(self):
        class Backend(DemoBackend):
            actions = []
            def call(self, role, payload, temperature):
                if role == 'executor':
                    self.actions.append((payload['node']['id'], temperature, payload['output_recovery']))
                    if len(self.actions) == 2:
                        raise ModelOutputError('finish_reason=length', response_preview='bounded partial output')
                    if len(self.actions) == 3:
                        raise ModelGenerationError('prediction aborted, token repeat limit reached')
                return super().call(role, payload, temperature)
        backend = Backend()
        result = Engine(backend, DemoTools()).run('fixture')
        self.assertEqual(result['answer'], '28')
        self.assertEqual([a[1] for a in backend.actions[:4]], [.7, .7, 0, 0])
        self.assertEqual([a[2] for a in backend.actions[:4]], [False, False, True, True])
        self.assertEqual(backend.actions[4][1:], (.7, False))
        self.assertTrue(all(c['status'] == 'Pass' for c in result['memory'].values()))
        errors = [e for e in result['events'] if e['event'] == 'model_output_error']
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[0]['response_preview'], 'bounded partial output')

    def test_persistent_repeat_abort_is_bounded_and_cannot_fabricate_success(self):
        class Backend(DemoBackend):
            count = 0
            def call(self, role, payload, temperature):
                if role == 'executor':
                    self.count += 1
                    raise ModelGenerationError('token repeat limit reached')
                return super().call(role, payload, temperature)
        backend = Backend()
        result = Engine(backend, DemoTools()).run('fixture')
        self.assertEqual(backend.count, 8)
        self.assertFalse(result['memory'])
        self.assertEqual(result['outcome'], 'abstained')
        self.assertTrue(any(e['event'] == 'blocked' for e in result['events']))

    def test_schema_avoids_nullable_branches_and_unbounded_decimal_confidence(self):
        schema = schema_for('executor', {'allowed_sources': ['fixture']})
        self.assertNotIn('anyOf', json.dumps(schema))
        self.assertNotIn('null', json.dumps(schema))
        self.assertEqual(schema['properties']['confidence']['enum'], [i / 10 for i in range(11)])
        args = schema['properties']['arguments']['properties']
        self.assertEqual(set(args), {'query', 'url', 'expression'})
        normalized = Engine(DemoBackend(), DemoTools())._action({
            'action': 'search', 'arguments': {'query': 'test', 'url': '', 'expression': ''},
            'result': '', 'evidence': [], 'confidence': 0})
        self.assertEqual(normalized, {'action': 'search', 'arguments': {'query': 'test'}})
