import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROFILE_VARIABLES = {
    "RUNTIME_PROFILE", "SCHEMA_PROVIDER", "CLIENT_GCP_PROJECT_ID", "PERSONAL_GCP_PROJECT_ID",
    "PERSONAL_GEMINI_PROJECT_ID", "PERSONAL_BIGQUERY_PROJECT_ID",
    "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE", "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE",
    "PERSONAL_GCP_ALLOWED_DATASETS", "PERSONAL_GCP_DATASET_MAPPINGS",
    "PERSONAL_GCP_ALLOWED_TABLES", "PERSONAL_GCP_ALLOWED_VIEWS", "PERSONAL_GCP_ALLOWED_COLUMNS_JSON",
    "PERSONAL_GCP_VIEW_DEFINITIONS_JSON", "PERSONAL_GCP_DEFAULT_DATASET_ID",
    "PERSONAL_GCP_DEFAULT_TABLE_ID", "PERSONAL_GCP_GEMINI_LOCATION",
    "PERSONAL_GCP_BIGQUERY_LOCATION", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
    "BIGQUERY_PROJECT_ID", "BIGQUERY_DATASET_ID", "BIGQUERY_TABLE_ID", "GEMINI_PROJECT_ID",
    "GEMINI_CREDENTIALS_FILE", "BIGQUERY_CREDENTIALS_FILE", "LOCAL_SCHEMA_BACKUP_PATH",
    "GOOGLE_APPLICATION_CREDENTIALS", "BIGQUERY_DYNAMIC_ALLOWED_DATASETS",
    "BIGQUERY_DYNAMIC_CATALOG_ENABLED", "BIGQUERY_DYNAMIC_ROUTING_ENABLED",
    "PERSONAL_GCP_EXECUTION_ENABLED", "BIGQUERY_MAX_BYTES_PROCESSED",
}


def run_python(code, overrides):
    environment = {key: value for key, value in os.environ.items() if key not in PROFILE_VARIABLES}
    environment.update(overrides)
    return subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )


class RuntimeProfileTests(unittest.TestCase):
    def test_legacy_default_and_client_provider_alias_resolve_to_client_profile(self):
        for settings in ({}, {"SCHEMA_PROVIDER": "CLIENT_GCP"}):
            with self.subTest(settings=settings):
                result = run_python(
                    "import config; assert config.RUNTIME_PROFILE == 'CLIENT_GCP'; "
                    "assert config.SCHEMA_PROVIDER == 'CLIENT_GCP'; "
                    "assert config.PROJECT_ID == 'mos-gcp-host-dev-prj'; "
                    "assert config.BIGQUERY_PROJECT_ID == config.PROJECT_ID",
                    settings,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_client_project_precedes_ambient_project_without_changing_defaults(self):
        result = run_python(
            "import config; assert config.PROJECT_ID == 'explicit-client-project'; "
            "assert config.BIGQUERY_PROJECT_ID == 'explicit-client-project'",
            {"RUNTIME_PROFILE": "CLIENT_GCP", "CLIENT_GCP_PROJECT_ID": "explicit-client-project",
             "GOOGLE_CLOUD_PROJECT": "unrelated-ambient-project"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_local_provider_alias_preserves_local_preview_configuration(self):
        result = run_python(
            "import config; assert config.RUNTIME_PROFILE == 'LOCAL'; "
            "assert config.SCHEMA_PROVIDER == 'LOCAL'; "
            "assert config.GEMINI_EFFECTIVE_PROJECT_ID == 'personal-gemini-project'; "
            "assert config.PERSONAL_GCP_EXECUTION_ENABLED is False",
            {
                "SCHEMA_PROVIDER": "LOCAL",
                "GEMINI_PROJECT_ID": "personal-gemini-project",
                "LOCAL_SCHEMA_BACKUP_PATH": "/approved/metadata",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_conflicting_runtime_and_legacy_profile_selectors_are_rejected(self):
        result = run_python("import config", {"RUNTIME_PROFILE": "LOCAL", "SCHEMA_PROVIDER": "CLIENT_GCP"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts with the legacy SCHEMA_PROVIDER", result.stderr)

    def test_personal_profile_requires_explicit_project_credentials_and_dataset_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            gemini_file = Path(directory, "gemini.credentials")
            bigquery_file = Path(directory, "bigquery.credentials")
            gemini_file.write_text("test fixture only", encoding="utf-8")
            bigquery_file.write_text("test fixture only", encoding="utf-8")
            valid = {
                "RUNTIME_PROFILE": "PERSONAL_GCP",
                "PERSONAL_GCP_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GEMINI_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_BIGQUERY_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE": str(gemini_file),
                "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE": str(bigquery_file),
                "PERSONAL_GCP_ALLOWED_DATASETS": "dw_nsp_api_views",
                "PERSONAL_GCP_DATASET_MAPPINGS": '{"client_nsp_views":"dw_nsp_api_views"}',
                "PERSONAL_GCP_ALLOWED_TABLES": '{"dw_nsp_api_views":["nsp_source"]}',
                "PERSONAL_GCP_ALLOWED_VIEWS": '{"dw_nsp_api_views":["nsp_base_view_01"]}',
                "PERSONAL_GCP_ALLOWED_COLUMNS_JSON": '{"dw_nsp_api_views.nsp_base_view_01":[{"name":"sport","data_type":"STRING","nullability":"NULLABLE"}],"dw_nsp_api_views.nsp_source":[{"name":"sport","data_type":"STRING","nullability":"NULLABLE"}]}',
                "PERSONAL_GCP_VIEW_DEFINITIONS_JSON": '{"dw_nsp_api_views.nsp_base_view_01":"SELECT sport FROM `mos-gcp-host-dev-prj-512311.dw_nsp_api_views.nsp_source`"}',
                "PERSONAL_GCP_DEFAULT_DATASET_ID": "dw_nsp_api_views",
                "PERSONAL_GCP_DEFAULT_TABLE_ID": "nsp_base_view_01",
                "PERSONAL_GCP_GEMINI_LOCATION": "global",
                "PERSONAL_GCP_BIGQUERY_LOCATION": "US",
            }
            required = (
                "PERSONAL_GCP_PROJECT_ID", "PERSONAL_GEMINI_PROJECT_ID", "PERSONAL_BIGQUERY_PROJECT_ID",
                "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE", "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE",
                "PERSONAL_GCP_ALLOWED_DATASETS", "PERSONAL_GCP_DATASET_MAPPINGS",
                "PERSONAL_GCP_ALLOWED_TABLES", "PERSONAL_GCP_ALLOWED_COLUMNS_JSON",
                "PERSONAL_GCP_DEFAULT_DATASET_ID",
                "PERSONAL_GCP_DEFAULT_TABLE_ID", "PERSONAL_GCP_GEMINI_LOCATION", "PERSONAL_GCP_BIGQUERY_LOCATION",
            )
            for variable in required:
                with self.subTest(missing=variable):
                    result = run_python("import config", {key: value for key, value in valid.items() if key != variable})
                    self.assertNotEqual(result.returncode, 0)
                    expected_error = (
                        "must contain approved datasets"
                        if variable == "PERSONAL_GCP_ALLOWED_DATASETS"
                        else "PERSONAL_GCP_DATASET_MAPPINGS is required"
                        if variable == "PERSONAL_GCP_DATASET_MAPPINGS"
                        else "must explicitly allow personal tables"
                        if variable == "PERSONAL_GCP_ALLOWED_TABLES"
                        else "PERSONAL_GCP_ALLOWED_COLUMNS_JSON is required"
                        if variable == "PERSONAL_GCP_ALLOWED_COLUMNS_JSON"
                        else variable + " is required"
                    )
                    self.assertIn(expected_error, result.stderr)

    def test_personal_profile_projects_allowlist_and_execution_gate_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            gemini_file = Path(directory, "gemini.credentials")
            bigquery_file = Path(directory, "bigquery.credentials")
            gemini_file.write_text("test fixture only", encoding="utf-8")
            bigquery_file.write_text("test fixture only", encoding="utf-8")
            settings = {
                "RUNTIME_PROFILE": "PERSONAL_GCP",
                "CLIENT_GCP_PROJECT_ID": "mos-gcp-host-dev-prj",
                "PERSONAL_GCP_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GEMINI_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_BIGQUERY_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE": str(gemini_file),
                "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE": str(bigquery_file),
                "PERSONAL_GCP_ALLOWED_DATASETS": "approved_reporting,approved_reference",
                "PERSONAL_GCP_DATASET_MAPPINGS": '{"client_reporting":"approved_reporting","client_reference":"approved_reference"}',
                "PERSONAL_GCP_ALLOWED_TABLES": '{"approved_reporting":["nsp_base_view_01"]}',
                "PERSONAL_GCP_ALLOWED_VIEWS": '{}',
                "PERSONAL_GCP_ALLOWED_COLUMNS_JSON": '{"approved_reporting.nsp_base_view_01":[{"name":"sport","data_type":"STRING","nullability":"NULLABLE"}]}',
                "PERSONAL_GCP_DEFAULT_DATASET_ID": "approved_reporting",
                "PERSONAL_GCP_DEFAULT_TABLE_ID": "nsp_base_view_01",
                "PERSONAL_GCP_GEMINI_LOCATION": "global",
                "PERSONAL_GCP_BIGQUERY_LOCATION": "US",
            }
            result = run_python(
                "import config; assert config.RUNTIME_PROFILE == 'PERSONAL_GCP'; "
                "assert config.PROJECT_ID == 'mos-gcp-host-dev-prj-512311'; "
                "assert config.GEMINI_EFFECTIVE_PROJECT_ID == 'mos-gcp-host-dev-prj-512311'; "
                "assert config.BIGQUERY_PROJECT_ID == 'mos-gcp-host-dev-prj-512311'; "
                "assert config.map_client_dataset_to_personal('client_reporting') == 'approved_reporting'; "
                "assert config.BIGQUERY_DATASET_ID == 'approved_reporting'; "
                "assert config.BIGQUERY_DYNAMIC_ALLOWED_DATASETS == {'approved_reporting', 'approved_reference'}; "
                "assert config.GEMINI_CREDENTIALS_FILE != config.BIGQUERY_CREDENTIALS_FILE; "
                "assert config.PERSONAL_GCP_EXECUTION_ENABLED is False; "
                "assert config.BIGQUERY_DYNAMIC_CATALOG_ENABLED is False; "
                "assert config.BIGQUERY_DYNAMIC_ROUTING_ENABLED is False; "
                "assert config.MAX_BYTES_PROCESSED == 262144000",
                settings,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_view_definition_rejects_client_project_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            gemini_file = Path(directory, "gemini.credentials")
            bigquery_file = Path(directory, "bigquery.credentials")
            gemini_file.write_text("test fixture only", encoding="utf-8")
            bigquery_file.write_text("test fixture only", encoding="utf-8")
            settings = {
                "RUNTIME_PROFILE": "PERSONAL_GCP",
                "PERSONAL_GCP_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GEMINI_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_BIGQUERY_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE": str(gemini_file),
                "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE": str(bigquery_file),
                "PERSONAL_GCP_ALLOWED_DATASETS": "approved_reporting",
                "PERSONAL_GCP_DATASET_MAPPINGS": '{"client_reporting":"approved_reporting"}',
                "PERSONAL_GCP_ALLOWED_TABLES": '{"approved_reporting":["approved_table"]}',
                "PERSONAL_GCP_ALLOWED_VIEWS": '{"approved_reporting":["approved_view"]}',
                "PERSONAL_GCP_ALLOWED_COLUMNS_JSON": '{"approved_reporting.approved_table":[{"name":"sport","data_type":"STRING","nullability":"NULLABLE"}],"approved_reporting.approved_view":[{"name":"sport","data_type":"STRING","nullability":"NULLABLE"}]}',
                "PERSONAL_GCP_VIEW_DEFINITIONS_JSON": '{"approved_reporting.approved_view":"SELECT sport FROM `mos-gcp-host-dev-prj.dw_nsp_api_views.nsp_base_view_01`"}',
                "PERSONAL_GCP_DEFAULT_DATASET_ID": "approved_reporting",
                "PERSONAL_GCP_DEFAULT_TABLE_ID": "approved_table",
                "PERSONAL_GCP_GEMINI_LOCATION": "global",
                "PERSONAL_GCP_BIGQUERY_LOCATION": "US",
            }
            result = run_python("import config", settings)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("approved personal-project datasets", result.stderr)

    def test_personal_profile_rejects_client_projects_ambient_adc_and_credential_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            gemini_file = Path(directory, "gemini.credentials")
            bigquery_file = Path(directory, "bigquery.credentials")
            gemini_file.write_text("test fixture only", encoding="utf-8")
            bigquery_file.write_text("test fixture only", encoding="utf-8")
            base = {
                "RUNTIME_PROFILE": "PERSONAL_GCP",
                "CLIENT_GCP_PROJECT_ID": "mos-gcp-host-dev-prj",
                "PERSONAL_GCP_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GEMINI_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_BIGQUERY_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE": str(gemini_file),
                "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE": str(bigquery_file),
                "PERSONAL_GCP_ALLOWED_DATASETS": "approved_reporting",
                "PERSONAL_GCP_DATASET_MAPPINGS": '{"client_reporting":"approved_reporting"}',
                "PERSONAL_GCP_ALLOWED_TABLES": '{"approved_reporting":["nsp_base_view_01"]}',
                "PERSONAL_GCP_ALLOWED_VIEWS": '{}',
                "PERSONAL_GCP_ALLOWED_COLUMNS_JSON": '{"approved_reporting.nsp_base_view_01":[{"name":"sport","data_type":"STRING","nullability":"NULLABLE"}]}',
                "PERSONAL_GCP_DEFAULT_DATASET_ID": "approved_reporting",
                "PERSONAL_GCP_DEFAULT_TABLE_ID": "nsp_base_view_01",
                "PERSONAL_GCP_GEMINI_LOCATION": "global",
                "PERSONAL_GCP_BIGQUERY_LOCATION": "US",
            }
            invalid_overrides = (
                ({"PERSONAL_BIGQUERY_PROJECT_ID": "mos-gcp-host-dev-prj"}, "must not equal CLIENT_GCP_PROJECT_ID"),
                ({"GOOGLE_CLOUD_PROJECT": "mos-gcp-host-dev-prj"}, "conflicts with the explicit PERSONAL_GCP project settings"),
                ({"GOOGLE_APPLICATION_CREDENTIALS": str(gemini_file)}, "not allowed in PERSONAL_GCP"),
                ({"PERSONAL_GCP_EXECUTION_ENABLED": "true"}, "cannot be enabled in this checkpoint"),
                ({"PERSONAL_GCP_GEMINI_CREDENTIALS_FILE": str(REPOSITORY_ROOT / "config.py")}, "must not point inside the source repository"),
                ({"PERSONAL_GCP_DEFAULT_DATASET_ID": "not_approved"}, "must be in PERSONAL_GCP_ALLOWED_DATASETS"),
                ({"PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE": str(gemini_file)}, "credential files must be distinct"),
                ({"PERSONAL_GCP_DATASET_MAPPINGS": '{"client_reporting":"approved_reporting","CLIENT_REPORTING":"approved_reporting"}'}, "duplicate client dataset IDs"),
                ({"PERSONAL_GCP_DATASET_MAPPINGS": '{"client_a":"unmapped_personal_dataset"}'}, "targets must exactly match"),
                ({"PERSONAL_GCP_DATASET_MAPPINGS": '{"client_reporting":"other_personal_dataset"}'}, "targets must exactly match"),
            )
            for overrides, expected_error in invalid_overrides:
                with self.subTest(overrides=overrides):
                    result = run_python("import config", {**base, **overrides})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected_error, result.stderr)

    def test_gemini_and_bigquery_credential_loaders_use_distinct_explicit_files(self):
        with tempfile.TemporaryDirectory() as directory:
            gemini_file = Path(directory, "gemini.credentials")
            bigquery_file = Path(directory, "bigquery.credentials")
            gemini_file.write_text("test fixture only", encoding="utf-8")
            bigquery_file.write_text("test fixture only", encoding="utf-8")
            settings = {
                "RUNTIME_PROFILE": "PERSONAL_GCP",
                "PERSONAL_GCP_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GEMINI_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_BIGQUERY_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE": str(gemini_file),
                "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE": str(bigquery_file),
                "PERSONAL_GCP_ALLOWED_DATASETS": "approved_reporting",
                "PERSONAL_GCP_DATASET_MAPPINGS": '{"client_reporting":"approved_reporting"}',
                "PERSONAL_GCP_ALLOWED_TABLES": '{"approved_reporting":["nsp_base_view_01"]}',
                "PERSONAL_GCP_ALLOWED_VIEWS": '{}',
                "PERSONAL_GCP_ALLOWED_COLUMNS_JSON": '{"approved_reporting.nsp_base_view_01":[{"name":"sport","data_type":"STRING","nullability":"NULLABLE"}]}',
                "PERSONAL_GCP_DEFAULT_DATASET_ID": "approved_reporting",
                "PERSONAL_GCP_DEFAULT_TABLE_ID": "nsp_base_view_01",
                "PERSONAL_GCP_GEMINI_LOCATION": "global",
                "PERSONAL_GCP_BIGQUERY_LOCATION": "US",
            }
            code = (
                "from unittest.mock import patch; import config; "
                "sentinel=object(); patcher=patch('google.auth.load_credentials_from_file', "
                "side_effect=[(sentinel, None), (sentinel, None)]); load=patcher.start(); "
                "assert config.load_gemini_credentials() is sentinel; "
                "assert config.load_bigquery_credentials() is sentinel; "
                "assert load.call_args_list[0].args[0] == config.GEMINI_CREDENTIALS_FILE; "
                "assert load.call_args_list[1].args[0] == config.BIGQUERY_CREDENTIALS_FILE; "
                "patcher.stop()"
            )
            result = run_python(code, settings)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_client_constructors_receive_only_their_explicit_credential_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            gemini_file = Path(directory, "gemini.credentials")
            bigquery_file = Path(directory, "bigquery.credentials")
            gemini_file.write_text("test fixture only", encoding="utf-8")
            bigquery_file.write_text("test fixture only", encoding="utf-8")
            settings = {
                "RUNTIME_PROFILE": "CLIENT_GCP",
                "CLIENT_GCP_PROJECT_ID": "mos-gcp-host-dev-prj",
                "GEMINI_CREDENTIALS_FILE": str(gemini_file),
                "BIGQUERY_CREDENTIALS_FILE": str(bigquery_file),
            }
            code = (
                "from unittest.mock import patch; import google.genai, google.cloud.bigquery; "
                "gemini_credentials=object(); bigquery_credentials=object(); "
                "loader_patch=patch('google.auth.load_credentials_from_file', side_effect=[(gemini_credentials,None),(bigquery_credentials,None)]); "
                "loader=loader_patch.start(); gemini_patch=patch('google.genai.Client'); gemini_client=gemini_patch.start(); "
                "bigquery_patch=patch('google.cloud.bigquery.Client'); bigquery_client=bigquery_patch.start(); "
                "import gemini_service, bigquery_database; "
                "gemini_service.client._get_client(); bigquery_database.client.query('SELECT 1'); "
                "assert gemini_client.call_args.kwargs['project']=='mos-gcp-host-dev-prj'; "
                "assert gemini_client.call_args.kwargs['credentials'] is gemini_credentials; "
                "assert bigquery_client.call_args.kwargs['project']=='mos-gcp-host-dev-prj'; "
                "assert bigquery_client.call_args.kwargs['credentials'] is bigquery_credentials; "
                "assert loader.call_args_list[0].args[0] == __import__('config').GEMINI_CREDENTIALS_FILE; "
                "assert loader.call_args_list[1].args[0] == __import__('config').BIGQUERY_CREDENTIALS_FILE"
            )
            result = run_python(code, settings)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_profile_constructs_no_clients_catalog_or_execution_path(self):
        with tempfile.TemporaryDirectory() as directory:
            gemini_file = Path(directory, "gemini.credentials")
            bigquery_file = Path(directory, "bigquery.credentials")
            gemini_file.write_text("test fixture only", encoding="utf-8")
            bigquery_file.write_text("test fixture only", encoding="utf-8")
            settings = {
                "RUNTIME_PROFILE": "PERSONAL_GCP",
                "PERSONAL_GCP_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GEMINI_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_BIGQUERY_PROJECT_ID": "mos-gcp-host-dev-prj-512311",
                "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE": str(gemini_file),
                "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE": str(bigquery_file),
                "PERSONAL_GCP_ALLOWED_DATASETS": "dw_nsp_api_views",
                "PERSONAL_GCP_DATASET_MAPPINGS": '{"dw_nsp_api_views":"dw_nsp_api_views"}',
                "PERSONAL_GCP_ALLOWED_TABLES": '{"dw_nsp_api_views":["nsp_source"]}',
                "PERSONAL_GCP_ALLOWED_VIEWS": '{"dw_nsp_api_views":["nsp_base_view_01"]}',
                "PERSONAL_GCP_ALLOWED_COLUMNS_JSON": '{"dw_nsp_api_views.nsp_base_view_01":[{"name":"sport","data_type":"STRING","nullability":"NULLABLE"}],"dw_nsp_api_views.nsp_source":[{"name":"sport","data_type":"STRING","nullability":"NULLABLE"}]}',
                "PERSONAL_GCP_VIEW_DEFINITIONS_JSON": '{"dw_nsp_api_views.nsp_base_view_01":"SELECT sport FROM `mos-gcp-host-dev-prj-512311.dw_nsp_api_views.nsp_source`"}',
                "PERSONAL_GCP_DEFAULT_DATASET_ID": "dw_nsp_api_views",
                "PERSONAL_GCP_DEFAULT_TABLE_ID": "nsp_base_view_01",
                "PERSONAL_GCP_GEMINI_LOCATION": "global",
                "PERSONAL_GCP_BIGQUERY_LOCATION": "US",
            }
            code = r"""
import socket
from unittest.mock import patch
with patch.object(socket.socket, 'connect', side_effect=AssertionError('network attempted')) as connect:
    with patch('socket.getaddrinfo', side_effect=AssertionError('DNS attempted')) as dns:
        with patch('google.genai.Client', side_effect=AssertionError('Gemini client constructed')) as gemini_client:
            with patch('google.cloud.bigquery.Client', side_effect=AssertionError('BigQuery client constructed')) as bigquery_client:
                import app
                import bigquery_database
                import gemini_service
                import schema_metadata
                import view_catalog
                from view_catalog import find_source
                from bigquery_access import BigQueryAccessDisabledError, GeminiAccessDisabledError
                assert bigquery_database.client is None
                assert view_catalog.get_source('budget_view_01') is None
                assert find_source('mos-gcp-host-dev-prj-512311', 'dw_sharepoint_views', 'budget_view_01') is None
                assert view_catalog.get_source('nsp_base_view_01') is not None
                prompt_schema = schema_metadata.get_bigquery_schema('nsp_base_view_01')
                assert '`mos-gcp-host-dev-prj-512311.dw_nsp_api_views.nsp_base_view_01`' in prompt_schema
                assert 'Client dataset(s) `dw_nsp_api_views` are explicitly mapped' in prompt_schema
                for discovery in (view_catalog.get_discovered_sources, view_catalog.get_dynamic_authorized_sources):
                    try:
                        discovery()
                    except BigQueryAccessDisabledError:
                        pass
                    else:
                        raise AssertionError('PERSONAL_GCP catalog discovery was not blocked')
                try:
                    bigquery_database.execute_sql('SELECT 1')
                except BigQueryAccessDisabledError:
                    pass
                else:
                    raise AssertionError('PERSONAL_GCP query execution was not blocked')
                try:
                    gemini_service.client.models.generate_content(model='test', contents='test')
                except GeminiAccessDisabledError:
                    pass
                else:
                    raise AssertionError('PERSONAL_GCP inference was not blocked')
                from fastapi.testclient import TestClient
                client = TestClient(app.app, raise_server_exceptions=False, client=('127.0.0.1', 50000))
                response = client.post('/ask', json={'question': 'anything'})
                assert response.status_code == 503
                remote = TestClient(app.app, raise_server_exceptions=False, client=('203.0.113.10', 50000))
                assert remote.post('/ask', json={'question': 'anything'}).status_code == 403
                gemini_client.assert_not_called()
                bigquery_client.assert_not_called()
                connect.assert_not_called()
                dns.assert_not_called()
"""
            result = run_python(code, settings)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()