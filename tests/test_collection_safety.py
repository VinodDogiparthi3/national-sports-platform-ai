import os
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class TestCollectionSafety(unittest.TestCase):
    def test_legacy_bigquery_modules_import_without_cloud_clients_or_network(self):
        script = r"""
import socket
from unittest.mock import patch
import google.cloud.bigquery
import google.genai

with patch('google.cloud.bigquery.Client', side_effect=AssertionError('BigQuery client constructed')) as bigquery_client:
    with patch('google.genai.Client', side_effect=AssertionError('Gemini client constructed')) as gemini_client:
        import tests.test_bigquery_connection
        import tests.test_bigquery_dry_run
        import tests.test_bigquery_execute
        import tests.test_bigquery_full_flow
        import tests.test_bigquery_nl_to_sql
        import tests.test_bigquery_guardrails
        import app
        import bigquery_database
        import gemini_service
        assert bigquery_database.client._client is None
        bigquery_client.assert_not_called()
        gemini_client.assert_not_called()
        assert socket.socket.connect.__module__ == 'sitecustomize'
        for blocked in (lambda: socket.socket().connect(('127.0.0.1', 9)), lambda: socket.getaddrinfo('example.invalid', 443)):
            try:
                blocked()
            except AssertionError:
                pass
            else:
                raise AssertionError('startup network guard is not active')
"""
        environment = {
            key: value for key, value in os.environ.items()
            if key not in {"GOOGLE_APPLICATION_CREDENTIALS", "SCHEMA_PROVIDER", "RUNTIME_PROFILE"}
        }
        environment.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "RUNTIME_PROFILE": "CLIENT_GCP",
            "PYTHONPATH": os.pathsep.join((str(REPOSITORY_ROOT / "tests"), str(REPOSITORY_ROOT))),
        })
        result = subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=REPOSITORY_ROOT,
            env=environment, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()