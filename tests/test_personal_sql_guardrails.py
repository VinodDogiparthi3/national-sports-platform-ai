import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class PersonalSqlGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        gemini_file = Path(self.tempdir.name, "gemini.credentials")
        bigquery_file = Path(self.tempdir.name, "bigquery.credentials")
        gemini_file.write_text("offline test fixture", encoding="utf-8")
        bigquery_file.write_text("offline test fixture", encoding="utf-8")
        self.environment = {
            key: value for key, value in os.environ.items()
            if key not in {
                "RUNTIME_PROFILE", "SCHEMA_PROVIDER", "PERSONAL_GCP_PROJECT_ID",
                "PERSONAL_GEMINI_PROJECT_ID", "PERSONAL_BIGQUERY_PROJECT_ID",
                "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE", "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE",
                "PERSONAL_GCP_ALLOWED_DATASETS", "PERSONAL_GCP_DATASET_MAPPINGS",
                "PERSONAL_GCP_ALLOWED_TABLES", "PERSONAL_GCP_ALLOWED_VIEWS",
                "PERSONAL_GCP_ALLOWED_COLUMNS_JSON", "PERSONAL_GCP_VIEW_DEFINITIONS_JSON",
                "PERSONAL_GCP_DEFAULT_DATASET_ID", "PERSONAL_GCP_DEFAULT_TABLE_ID",
                "PERSONAL_GCP_GEMINI_LOCATION", "PERSONAL_GCP_BIGQUERY_LOCATION",
                "GOOGLE_CLOUD_PROJECT", "GOOGLE_APPLICATION_CREDENTIALS",
            }
        }
        self.environment.update({
            "RUNTIME_PROFILE": "PERSONAL_GCP",
            "PERSONAL_GCP_PROJECT_ID": "personal-project",
            "PERSONAL_GEMINI_PROJECT_ID": "personal-project",
            "PERSONAL_BIGQUERY_PROJECT_ID": "personal-project",
            "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE": str(gemini_file),
            "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE": str(bigquery_file),
            "PERSONAL_GCP_ALLOWED_DATASETS": "approved_a,approved_b",
            "PERSONAL_GCP_DATASET_MAPPINGS": json.dumps({
                "client_dataset_a": "approved_a",
                "client_dataset_b": "approved_b",
            }),
            "PERSONAL_GCP_ALLOWED_TABLES": json.dumps({
                "approved_a": ["source_a"],
                "approved_b": ["source_b"],
            }),
            "PERSONAL_GCP_ALLOWED_VIEWS": "{}",
            "PERSONAL_GCP_ALLOWED_COLUMNS_JSON": json.dumps({
                "approved_a.source_a": [
                    {"name": "id", "data_type": "STRING", "nullability": "REQUIRED"},
                    {"name": "sport", "data_type": "STRING", "nullability": "NULLABLE"},
                    {"name": "الرياضة", "data_type": "STRING", "nullability": "NULLABLE"},
                ],
                "approved_b.source_b": [
                    {"name": "id", "data_type": "STRING", "nullability": "REQUIRED"},
                    {"name": "sport", "data_type": "STRING", "nullability": "NULLABLE"},
                ],
            }),
            "PERSONAL_GCP_DEFAULT_DATASET_ID": "approved_a",
            "PERSONAL_GCP_DEFAULT_TABLE_ID": "source_a",
            "PERSONAL_GCP_GEMINI_LOCATION": "global",
            "PERSONAL_GCP_BIGQUERY_LOCATION": "US",
        })

    def validate_in_subprocess(self, sql: str, *, should_pass: bool) -> subprocess.CompletedProcess:
        code = f"""
import socket
from unittest.mock import patch
from google.cloud import bigquery
from sql_guardrails import validate_sql
from sql_guardrails import OutOfScopeQueryError
with patch.object(socket.socket, 'connect', side_effect=AssertionError('network attempted')) as connect:
    with patch('socket.getaddrinfo', side_effect=AssertionError('DNS attempted')) as dns:
        with patch('google.cloud.bigquery.Client', side_effect=AssertionError('client constructed')) as client:
            sql = {sql!r}
            try:
                result = validate_sql(sql)
                assert {should_pass!r}, 'SQL unexpectedly passed authorization'
                assert 'personal-project.' in result
            except (ValueError, OutOfScopeQueryError):
                assert not {should_pass!r}, 'authorized SQL was rejected'
            client.assert_not_called()
            connect.assert_not_called()
            dns.assert_not_called()
"""
        return subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=REPOSITORY_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_valid_mapped_personal_source_passes_and_is_personal_project_qualified(self):
        result = self.validate_in_subprocess(
            "SELECT sport FROM `personal-project.approved_a.source_a`",
            should_pass=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_valid_unicode_column_from_explicit_schema_passes(self):
        result = self.validate_in_subprocess(
            "SELECT `الرياضة` FROM `personal-project.approved_a.source_a`",
            should_pass=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_valid_cte_and_nested_subquery_resolve_to_approved_personal_sources(self):
        sql = (
            "WITH selected AS (SELECT sport FROM `personal-project.approved_a.source_a`) "
            "SELECT sport FROM selected WHERE EXISTS "
            "(SELECT 1 FROM `personal-project.approved_a.source_a` nested WHERE nested.id = selected.sport)"
        )
        result = self.validate_in_subprocess(sql, should_pass=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unapproved_dataset_and_table_are_rejected(self):
        for sql in (
            "SELECT sport FROM `personal-project.unapproved.source_a`",
            "SELECT sport FROM `personal-project.approved_a.unapproved_table`",
        ):
            with self.subTest(sql=sql):
                result = self.validate_in_subprocess(sql, should_pass=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_client_project_is_rejected_in_direct_nested_cte_and_join_sources(self):
        queries = (
            "SELECT sport FROM `mos-gcp-host-dev-prj.approved_a.source_a`",
            "SELECT a.sport FROM `personal-project.approved_a.source_a` a JOIN `mos-gcp-host-dev-prj.approved_b.source_b` b ON a.id=b.id",
            "WITH c AS (SELECT sport FROM `mos-gcp-host-dev-prj.approved_a.source_a`) SELECT sport FROM c",
            "SELECT sport FROM `personal-project.approved_a.source_a` WHERE EXISTS (SELECT 1 FROM `mos-gcp-host-dev-prj.approved_b.source_b`)",
        )
        for sql in queries:
            with self.subTest(sql=sql):
                result = self.validate_in_subprocess(sql, should_pass=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cross_dataset_join_is_rejected_without_verified_relationship(self):
        sql = (
            "SELECT a.sport FROM `personal-project.approved_a.source_a` a "
            "JOIN `personal-project.approved_b.source_b` b ON a.id=b.id"
        )
        result = self.validate_in_subprocess(sql, should_pass=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()