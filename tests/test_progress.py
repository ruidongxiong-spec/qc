import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from scope.progress import log, stage


class ProgressTests(unittest.TestCase):
    def test_diagnostics_redact_credentials_and_report_failure(self):
        output = io.StringIO()
        with patch.dict('os.environ', {'SCOPE_VERBOSE': '1', 'SCOPE_API_KEY': 'test-secret'}), redirect_stderr(output):
            with self.assertRaisesRegex(ValueError, 'request failed'):
                with stage('model role=executor'):
                    log('test-secret https://user:password@proxy.example/path')
                    raise ValueError('request failed')
        text = output.getvalue()
        self.assertIn('START model role=executor', text)
        self.assertIn('FAIL model role=executor', text)
        self.assertIn('request failed', text)
        self.assertNotIn('test-secret', text)
        self.assertNotIn('user:password', text)

    def test_progress_is_quiet_by_default(self):
        output = io.StringIO()
        with patch.dict('os.environ', {'SCOPE_VERBOSE': ''}), redirect_stderr(output):
            with stage('test'):
                log('test')
        self.assertEqual(output.getvalue(), '')
