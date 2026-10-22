import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class BigQueryConnectionConfigurationTests(unittest.TestCase):
    def test_import_is_inert_and_first_query_uses_explicit_client_project(self):
        script = """
from unittest.mock import patch
import config
import bigquery_database
assert bigquery_database.client._client is None
with patch('google.cloud.bigquery.Client') as client:
    bigquery_database.client.query('SELECT 1')
    client.assert_called_once_with(project=config.BIGQUERY_PROJECT_ID)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=REPOSITORY_ROOT,
            env={**__import__("os").environ, "RUNTIME_PROFILE": "CLIENT_GCP", "SCHEMA_PROVIDER": "CLIENT_GCP"},
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()