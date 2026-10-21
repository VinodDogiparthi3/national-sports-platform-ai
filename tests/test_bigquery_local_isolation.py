import os
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def run_isolated(code: str, *, schema_provider: str) -> subprocess.CompletedProcess:
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {
            "RUNTIME_PROFILE", "SCHEMA_PROVIDER", "LOCAL_SCHEMA_BACKUP_PATH", "GEMINI_PROJECT_ID",
            "PERSONAL_GCP_PROJECT_ID", "PERSONAL_GEMINI_PROJECT_ID", "PERSONAL_BIGQUERY_PROJECT_ID",
            "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE", "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE",
            "PERSONAL_GCP_ALLOWED_DATASETS", "PERSONAL_GCP_DEFAULT_DATASET_ID",
            "PERSONAL_GCP_DEFAULT_TABLE_ID", "PERSONAL_GCP_GEMINI_LOCATION",
            "PERSONAL_GCP_BIGQUERY_LOCATION", "PERSONAL_GCP_EXECUTION_ENABLED",
        }
    }
    environment["SCHEMA_PROVIDER"] = schema_provider
    if schema_provider == "LOCAL":
        environment["LOCAL_SCHEMA_BACKUP_PATH"] = "/approved/test/metadata-backup"
        environment["GEMINI_PROJECT_ID"] = "approved-personal-gemini-project"
    return subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=REPOSITORY_ROOT,
        env=environment, capture_output=True, text=True, timeout=20,
    )


class LocalBigQueryIsolationTests(unittest.TestCase):
    def test_every_bigquery_access_path_fails_before_client_or_network(self):
        code = r"""
import socket
from unittest.mock import patch
with patch.object(socket.socket, 'connect', side_effect=AssertionError('network attempted')) as connect:
    with patch('socket.getaddrinfo', side_effect=AssertionError('DNS attempted')) as dns:
        import google.cloud.bigquery
        with patch('google.cloud.bigquery.Client', side_effect=AssertionError('BigQuery client created')) as client:
            import bigquery_access
            import bigquery_catalog
            import bigquery_database
            import schema_metadata
            import view_catalog
            from types import SimpleNamespace
            from bigquery_access import BigQueryAccessDisabledError

            assert bigquery_database.client is None
            try:
                schema_metadata.get_cached_source_columns(SimpleNamespace(source_id='unapproved.source'))
            except ValueError:
                pass
            else:
                raise AssertionError('LOCAL mode returned a schema for an unapproved source')
            blocked = [
                lambda: bigquery_database.dry_run_sql('SELECT 1'),
                lambda: bigquery_database._preflight_sqls(['SELECT 1']),
                lambda: bigquery_database._execute_sql_after_dry_run('SELECT 1'),
                lambda: bigquery_database.execute_sql_batch(['SELECT 1']),
                lambda: bigquery_database.execute_sql('SELECT 1'),
                lambda: bigquery_database._run_export_query('SELECT 1', 1),
                lambda: bigquery_database.get_export_row_count('SELECT 1'),
                lambda: bigquery_database.get_export_row_counts(['SELECT 1']),
                lambda: bigquery_database.stream_query_rows('SELECT 1'),
                lambda: bigquery_database.stream_query_rows_batch(['SELECT 1']),
                lambda: bigquery_catalog.discover_project_catalog(),
                lambda: bigquery_catalog._fetch_object(object(), 'dataset', object()),
                lambda: view_catalog.get_discovered_sources(),
                lambda: view_catalog.get_dynamic_authorized_sources(),
            ]
            for operation in blocked:
                try:
                    operation()
                except BigQueryAccessDisabledError:
                    pass
                else:
                    raise AssertionError('LOCAL BigQuery access path did not fail closed')
            assert schema_metadata.get_filter_values('nsp_base_view_01') is None
            assert view_catalog.get_source('nsp_base_view_01').source_id == 'nsp_base_view_01'
            assert view_catalog.get_source('dw_ms_crm_onprem.ntw_licenseBase').source_id == 'dw_ms_crm_onprem.ntw_licenseBase'
            assert view_catalog.get_source('dl_ms_crm_onprem.ntw_licenserequestBase') is None
            assert any(source.source_id == 'dw_ms_crm_onprem.ntw_licenseBase' for source in view_catalog.get_queryable_sources())
            assert client.call_count == 0, client.call_args_list
            connect.assert_not_called()
            dns.assert_not_called()
"""
        result = run_isolated(code, schema_provider="LOCAL")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_client_gcp_mode_constructs_same_client_with_existing_project_default(self):
        code = r"""
from unittest.mock import patch
import google.cloud.bigquery
with patch('google.cloud.bigquery.Client') as client:
    import config
    import bigquery_database
    assert config.SCHEMA_PROVIDER == 'CLIENT_GCP'
    assert config.BIGQUERY_PROJECT_ID == 'mos-gcp-host-dev-prj'
    client.assert_called_once_with(project='mos-gcp-host-dev-prj')
    assert bigquery_database.client is client.return_value
"""
        result = run_isolated(code, schema_provider="CLIENT_GCP")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()