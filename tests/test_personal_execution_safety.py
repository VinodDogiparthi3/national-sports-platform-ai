import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

import bigquery_database
import bigquery_dependencies
import gemini_service
import usage_limits
from bigquery_dependencies import DependencyVerificationError, verify_personal_dry_run
from request_safety import RequestSafetyMiddleware
from usage_limits import UsageLimitExceeded, consume_gemini_call, request_usage_scope, reserve_bigquery_job


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PERSONAL_PROJECT = "mos-gcp-host-dev-prj-512311"


def run_python(code, overrides):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PERSONAL_GCP_", "GEMINI_", "BIGQUERY_", "GOOGLE_"))
        and key not in {"RUNTIME_PROFILE", "SCHEMA_PROVIDER", "CLIENT_GCP_PROJECT_ID", "PERSONAL_GEMINI_PROJECT_ID",
                        "PERSONAL_BIGQUERY_PROJECT_ID", "LOCAL_SCHEMA_BACKUP_PATH"}
    }
    environment.update(overrides)
    return subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=REPOSITORY_ROOT, env=environment,
        capture_output=True, text=True, timeout=20,
    )


class PersonalConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        gemini_file = Path(self.directory.name, "gemini.credentials")
        bigquery_file = Path(self.directory.name, "bigquery.credentials")
        gemini_file.write_text("test fixture only", encoding="utf-8")
        bigquery_file.write_text("test fixture only", encoding="utf-8")
        self.personal = {
            "RUNTIME_PROFILE": "PERSONAL_GCP",
            "PERSONAL_GCP_PROJECT_ID": PERSONAL_PROJECT,
            "PERSONAL_GEMINI_PROJECT_ID": PERSONAL_PROJECT,
            "PERSONAL_BIGQUERY_PROJECT_ID": PERSONAL_PROJECT,
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

    def execution_settings(self, **overrides):
        settings = {
            **self.personal,
            "PERSONAL_GCP_ALLOWED_DATASETS": "nsp_ai_synthetic",
            "PERSONAL_GCP_DATASET_MAPPINGS": '{"client_nsp_api_views":"nsp_ai_synthetic"}',
            "PERSONAL_GCP_ALLOWED_TABLES": '{"nsp_ai_synthetic":["synthetic_athletes"]}',
            "PERSONAL_GCP_ALLOWED_VIEWS": "{}",
            "PERSONAL_GCP_VIEW_DEFINITIONS_JSON": "{}",
            "PERSONAL_GCP_ALLOWED_COLUMNS_JSON": (
                '{"nsp_ai_synthetic.synthetic_athletes":['
                '{"name":"synthetic_id","data_type":"STRING","nullability":"NULLABLE"},'
                '{"name":"sport","data_type":"STRING","nullability":"NULLABLE"},'
                '{"name":"age_years","data_type":"INT64","nullability":"NULLABLE"},'
                '{"name":"category","data_type":"STRING","nullability":"NULLABLE"},'
                '{"name":"country_label","data_type":"STRING","nullability":"NULLABLE"},'
                '{"name":"association_label","data_type":"STRING","nullability":"NULLABLE"},'
                '{"name":"registration_year","data_type":"INT64","nullability":"NULLABLE"}]}'
            ),
            "PERSONAL_GCP_DEFAULT_DATASET_ID": "nsp_ai_synthetic",
            "PERSONAL_GCP_DEFAULT_TABLE_ID": "synthetic_athletes",
            "PERSONAL_GCP_EXECUTION_ENABLED": "true",
            "PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT": PERSONAL_PROJECT,
        }
        settings.update(overrides)
        return settings

    def test_personal_defaults_are_bounded_and_execution_stays_disabled(self):
        result = run_python(
            "from unittest.mock import patch; import config; "
            "assert config.PERSONAL_GCP_EXECUTION_ENABLED is False; "
            "assert config.MAX_BYTES_PROCESSED == 262144000; "
            "assert config.BIGQUERY_JOB_LOCATION == 'US'; "
            "sentinel=object(); patcher = patch.object(config, 'load_bigquery_credentials', return_value=sentinel); patcher.start(); "
            "assert config.get_bigquery_client_kwargs() == {'project': config.PERSONAL_GCP_PROJECT_ID, 'location': 'US', 'credentials': sentinel}; "
            "assert config.BIGQUERY_JOB_LABELS == {'app': 'nsp-ai', 'runtime-profile': 'personal-gcp'}; "
            "assert config.BIGQUERY_JOB_TIMEOUT_SECONDS == 60; "
            "assert (config.GEMINI_MAX_OUTPUT_TOKENS, config.GEMINI_REQUEST_TIMEOUT_SECONDS, config.GEMINI_MAX_CALLS_PER_REQUEST) == (4096, 60, 8); "
            "assert config.PERSONAL_GCP_MAX_GEMINI_CALLS_PER_PROCESS == 200; "
            "assert config.PERSONAL_GCP_MAX_BIGQUERY_JOBS_PER_PROCESS == 100; "
            "assert config.PERSONAL_GCP_MAX_BIGQUERY_BYTES_PER_PROCESS == 4 * 262144000; "
            "assert config.PERSONAL_GCP_ALLOW_REMOTE_CLIENTS is False and config.PERSONAL_GCP_API_TOKEN is None; "
            "assert config.PERSONAL_GCP_RATE_LIMIT_PER_MINUTE == 10; "
            "assert config.PERSONAL_GCP_VIEW_DEPENDENCY_CLOSURE == {'dw_nsp_api_views.nsp_base_view_01': frozenset({'dw_nsp_api_views.nsp_source'})}",
            self.personal,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_gemini_never_falls_back_to_shared_adc(self):
        code = """
from unittest.mock import patch
import config
import gemini_service
from usage_limits import request_usage_scope

assert config.PERSONAL_GCP_EXECUTION_ENABLED is True
with patch.object(gemini_service, 'load_gemini_credentials', return_value=None), \\
     patch.object(gemini_service.genai, 'Client') as client:
    with request_usage_scope():
        try:
            gemini_service._LazyGeminiClient()._get_client()
        except ValueError as error:
            assert 'shared ADC fallback is disabled' in str(error)
        else:
            raise AssertionError('PERSONAL_GCP fell back to shared ADC')
    client.assert_not_called()
"""
        result = run_python(code, self.execution_settings())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_job_timeout_never_exceeds_sixty_seconds(self):
        for query_timeout, expected in (("300", 60), ("30", 30)):
            with self.subTest(query_timeout=query_timeout):
                result = run_python(
                    f"import config; assert config.BIGQUERY_JOB_TIMEOUT_SECONDS == {expected}, config.BIGQUERY_JOB_TIMEOUT_SECONDS",
                    {**self.personal, "BIGQUERY_QUERY_TIMEOUT_SECONDS": query_timeout},
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_rejects_unsafe_safeguard_settings(self):
        cases = (
            ({"PERSONAL_GCP_EXECUTION_ENABLED": "true"}, "must exactly match PERSONAL_GCP_PROJECT_ID"),
            ({"PERSONAL_GCP_BIGQUERY_LOCATION": "global"}, "not global"),
            ({"PERSONAL_GCP_BIGQUERY_LOCATION": "US;DROP"}, "not global"),
            ({"GEMINI_MAX_OUTPUT_TOKENS": "9000"}, "GEMINI_MAX_OUTPUT_TOKENS must be a positive integer no greater than 8192"),
            ({"GEMINI_REQUEST_TIMEOUT_SECONDS": "0"}, "GEMINI_REQUEST_TIMEOUT_SECONDS must be a positive integer"),
            ({"GEMINI_MAX_CALLS_PER_REQUEST": "21"}, "GEMINI_MAX_CALLS_PER_REQUEST must be a positive integer"),
            ({"BIGQUERY_JOB_TIMEOUT_SECONDS": "61"}, "BIGQUERY_JOB_TIMEOUT_SECONDS must be a positive integer no greater than 60"),
            ({"BIGQUERY_JOB_TIMEOUT_SECONDS": "61", "BIGQUERY_QUERY_TIMEOUT_SECONDS": "300"}, "no greater than 60"),
            ({"PERSONAL_GCP_MAX_BIGQUERY_BYTES_PER_PROCESS": "abc"}, "must be a positive integer"),
            ({"PERSONAL_GCP_RATE_LIMIT_PER_MINUTE": "0"}, "PERSONAL_GCP_RATE_LIMIT_PER_MINUTE must be a positive integer"),
            ({"PERSONAL_GCP_ALLOW_REMOTE_CLIENTS": "true"}, "PERSONAL_GCP_API_TOKEN is required"),
            ({"PERSONAL_GCP_API_TOKEN": "short"}, "at least 32 characters"),
        )
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                result = run_python("import config", {**self.personal, **overrides})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_personal_execution_requires_both_opt_in_values(self):
        confirmation_only = run_python(
            "import config; assert config.PERSONAL_GCP_EXECUTION_ENABLED is False",
            {**self.personal, "PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT": PERSONAL_PROJECT},
        )
        self.assertEqual(confirmation_only.returncode, 0, confirmation_only.stderr)

        for confirmation in (None, "another-project"):
            with self.subTest(confirmation=confirmation):
                settings = {**self.execution_settings(), "PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT": confirmation or ""}
                result = run_python("import config", settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT must exactly match", result.stderr)

    def test_personal_execution_accepts_exact_confirmation_for_synthetic_table_only(self):
        for integer_type in ("INT64", "INTEGER"):
            with self.subTest(integer_type=integer_type):
                settings = self.execution_settings()
                settings["PERSONAL_GCP_ALLOWED_COLUMNS_JSON"] = settings[
                    "PERSONAL_GCP_ALLOWED_COLUMNS_JSON"
                ].replace("INT64", integer_type)
                result = run_python(
                    "import config; assert config.PERSONAL_GCP_EXECUTION_ENABLED is True; "
                    "assert config.PERSONAL_GCP_ALLOWED_DATASETS == {'nsp_ai_synthetic'}; "
                    "assert config.PERSONAL_GCP_ALLOWED_TABLES == {'nsp_ai_synthetic': {'synthetic_athletes'}}; "
                    "assert config.PERSONAL_GCP_ALLOWED_VIEWS == {}; "
                    "assert config.PERSONAL_GCP_DEFAULT_DATASET_ID == 'nsp_ai_synthetic'; "
                    "assert config.PERSONAL_GCP_DEFAULT_TABLE_ID == 'synthetic_athletes'",
                    settings,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_execution_rejects_non_synthetic_dataset_or_table_allowlists(self):
        invalid = (
            {"PERSONAL_GCP_ALLOWED_DATASETS": "nsp_ai_synthetic,other_dataset"},
            {"PERSONAL_GCP_ALLOWED_TABLES": '{"nsp_ai_synthetic":["synthetic_athletes","other_table"]}'},
            {"PERSONAL_GCP_ALLOWED_VIEWS": '{"nsp_ai_synthetic":["approved_view"]}',
             "PERSONAL_GCP_VIEW_DEFINITIONS_JSON": '{"nsp_ai_synthetic.approved_view":"SELECT synthetic_id FROM `mos-gcp-host-dev-prj-512311.nsp_ai_synthetic.synthetic_athletes`"}',
             "PERSONAL_GCP_ALLOWED_COLUMNS_JSON": (
                 '{"nsp_ai_synthetic.synthetic_athletes":['
                 '{"name":"synthetic_id","data_type":"STRING","nullability":"NULLABLE"},'
                 '{"name":"sport","data_type":"STRING","nullability":"NULLABLE"},'
                 '{"name":"age_years","data_type":"INT64","nullability":"NULLABLE"},'
                 '{"name":"category","data_type":"STRING","nullability":"NULLABLE"},'
                 '{"name":"country_label","data_type":"STRING","nullability":"NULLABLE"},'
                 '{"name":"association_label","data_type":"STRING","nullability":"NULLABLE"},'
                 '{"name":"registration_year","data_type":"INT64","nullability":"NULLABLE"}],'
                 '"nsp_ai_synthetic.approved_view":[{"name":"synthetic_id","data_type":"STRING","nullability":"NULLABLE"}]}'
             )},
            {"PERSONAL_GCP_DEFAULT_TABLE_ID": "other_table"},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                result = run_python("import config", self.execution_settings(**overrides))
                self.assertNotEqual(result.returncode, 0)

    def test_client_profile_keeps_existing_defaults(self):
        result = run_python(
            "from unittest.mock import patch; import config; "
            "patcher = patch.object(config, 'load_bigquery_credentials', return_value=None); patcher.start(); "
            "assert config.get_bigquery_client_kwargs() == {'project': 'mos-gcp-host-dev-prj'}; "
            "assert config.BIGQUERY_JOB_LOCATION is None; "
            "assert config.BIGQUERY_JOB_TIMEOUT_SECONDS is None; "
            "assert config.BIGQUERY_JOB_LABELS['runtime-profile'] == 'client-gcp'; "
            "assert (config.GEMINI_MAX_OUTPUT_TOKENS, config.GEMINI_REQUEST_TIMEOUT_SECONDS, config.GEMINI_MAX_CALLS_PER_REQUEST) == (None, None, None); "
            "assert config.PERSONAL_GCP_MAX_BIGQUERY_BYTES_PER_PROCESS is None; "
            "assert config.PERSONAL_GCP_RATE_LIMIT_PER_MINUTE is None",
            {"RUNTIME_PROFILE": "CLIENT_GCP"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_client_profile_uses_explicit_bigquery_location_only(self):
        result = run_python(
            "from unittest.mock import patch; import config; "
            "patcher = patch.object(config, 'load_bigquery_credentials', return_value=None); patcher.start(); "
            "assert config.get_bigquery_client_kwargs()['location'] == 'EU'",
            {"RUNTIME_PROFILE": "CLIENT_GCP", "BIGQUERY_LOCATION": "EU", "GOOGLE_CLOUD_LOCATION": "global"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_enabled_personal_clients_are_lazy(self):
        code = """
from unittest.mock import patch
import config

assert config.PERSONAL_GCP_EXECUTION_ENABLED is True
with patch('google.cloud.bigquery.Client') as bq, patch('google.genai.Client') as gemini:
    import bigquery_database
    import gemini_service
    assert bigquery_database.client is not None
    assert bigquery_database.client._client is None
    assert gemini_service.client.__class__.__name__ == '_LazyGeminiClient'
    assert gemini_service.client._client is None
    bq.assert_not_called()
    gemini.assert_not_called()
"""
        result = run_python(code, self.execution_settings())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_credential_loaders_validate_target_and_quota_without_refresh(self):
        code = r"""
from unittest.mock import Mock, patch
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.impersonated_credentials import Credentials as ImpersonatedCredentials
import config

project = config.PERSONAL_GCP_PROJECT_ID
source = UserCredentials(
    token=None,
    refresh_token='offline-fixture-only',
    client_id='offline-fixture-client',
    client_secret='offline-fixture-secret',
    quota_project_id=project,
)
source.refresh = Mock(side_effect=AssertionError('offline test attempted token refresh'))
gemini = ImpersonatedCredentials(
    source, 'nsp-ai-gemini@' + project + '.iam.gserviceaccount.com',
    ['https://www.googleapis.com/auth/cloud-platform'], quota_project_id=project,
)
bigquery = ImpersonatedCredentials(
    source, 'nsp-ai-bigquery@' + project + '.iam.gserviceaccount.com',
    ['https://www.googleapis.com/auth/cloud-platform'], quota_project_id=project,
)
with patch('google.auth.load_credentials_from_file', side_effect=[(gemini, None), (bigquery, None)]) as loader:
    assert config.load_gemini_credentials() is gemini
    assert config.load_bigquery_credentials() is bigquery
    assert loader.call_args_list[0].args[0] == config.GEMINI_CREDENTIALS_FILE
    assert loader.call_args_list[1].args[0] == config.BIGQUERY_CREDENTIALS_FILE
    assert source.token is None
    source.refresh.assert_not_called()
"""
        result = run_python(code, self.execution_settings())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_credential_loaders_reject_wrong_target_or_quota_project(self):
        cases = (
            ("gemini", "other-gemini@mos-gcp-host-dev-prj-512311.iam.gserviceaccount.com", PERSONAL_PROJECT,
             "Gemini credentials target the wrong service account"),
            ("gemini", "nsp-ai-gemini@mos-gcp-host-dev-prj-512311.iam.gserviceaccount.com", "other-project",
             "Gemini credentials must use the personal quota project"),
            ("bigquery", "other-bq@mos-gcp-host-dev-prj-512311.iam.gserviceaccount.com", PERSONAL_PROJECT,
             "BigQuery credentials target the wrong service account"),
            ("bigquery", "nsp-ai-bigquery@mos-gcp-host-dev-prj-512311.iam.gserviceaccount.com", "other-project",
             "BigQuery credentials must use the personal quota project"),
        )
        for service, target, quota_project, expected in cases:
            code = f"""
from unittest.mock import Mock, patch
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.impersonated_credentials import Credentials as ImpersonatedCredentials
import config

source = UserCredentials(token=None, refresh_token='offline-fixture', client_id='fixture', client_secret='fixture')
source.refresh = Mock(side_effect=AssertionError('token refresh forbidden'))
credential = ImpersonatedCredentials(
    source, {target!r}, ['https://www.googleapis.com/auth/cloud-platform'], quota_project_id={quota_project!r}
)
loader = config.load_gemini_credentials if {service!r} == 'gemini' else config.load_bigquery_credentials
with patch('google.auth.load_credentials_from_file', return_value=(credential, None)):
    try:
        loader()
    except ValueError as error:
        assert {expected!r} in str(error)
    else:
        raise AssertionError('credential mismatch was accepted')
source.refresh.assert_not_called()
"""
            with self.subTest(service=service, target=target, quota_project=quota_project):
                result = run_python(code, self.execution_settings())
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_personal_credential_loaders_reject_non_impersonated_credentials(self):
        code = """
from unittest.mock import patch
from google.oauth2.credentials import Credentials as UserCredentials
import config

credential = UserCredentials(token=None, refresh_token='offline-fixture', client_id='fixture', client_secret='fixture')
with patch('google.auth.load_credentials_from_file', return_value=(credential, None)):
    try:
        config.load_gemini_credentials()
    except ValueError as error:
        assert 'must use service-account impersonation' in str(error)
    else:
        raise AssertionError('non-impersonated credential was accepted')
"""
        result = run_python(code, self.execution_settings())
        self.assertEqual(result.returncode, 0, result.stderr)


class BigQueryJobSafeguardTests(unittest.TestCase):
    def _run_execute(self):
        dry_job = Mock(total_bytes_processed=10)
        result = MagicMock(schema=[])
        result.__iter__.return_value = iter(())
        result_job = Mock()
        result_job.result.return_value = result
        client = Mock()
        client.query.side_effect = [dry_job, result_job]
        with patch.object(bigquery_database, "client", client):
            bigquery_database.execute_sql("SELECT 1")
        return client.query.call_args_list

    def test_client_jobs_carry_labels_without_server_timeout_and_preserve_byte_cap(self):
        calls = self._run_execute()
        self.assertEqual(len(calls), 2)
        for call in calls:
            config = call.kwargs["job_config"]
            self.assertEqual(config.labels, {"app": "nsp-ai", "runtime-profile": "client-gcp"})
            self.assertIsNone(config.job_timeout_ms)
            self.assertNotIn("location", call.kwargs)
        self.assertTrue(calls[0].kwargs["job_config"].dry_run)
        self.assertEqual(calls[1].kwargs["job_config"].maximum_bytes_billed, 262_144_000)

    def test_configured_job_timeout_is_sent_with_every_job(self):
        with patch.object(bigquery_database, "BIGQUERY_JOB_TIMEOUT_SECONDS", 60):
            calls = self._run_execute()
        self.assertEqual([int(call.kwargs["job_config"].job_timeout_ms) for call in calls], [60_000, 60_000])

    def test_configured_location_is_sent_with_every_job(self):
        with patch.object(bigquery_database, "BIGQUERY_JOB_LOCATION", "EU"):
            calls = self._run_execute()
        self.assertEqual([call.kwargs["location"] for call in calls], ["EU", "EU"])


def _reference(project, dataset, table):
    return SimpleNamespace(project=project, dataset_id=dataset, table_id=table)


class DependencyVerificationTests(unittest.TestCase):
    SQL = f"SELECT sport FROM `{PERSONAL_PROJECT}.personal_ds.approved_view`"

    def setUp(self):
        for name, value in {
            "PERSONAL_GCP_PROJECT_ID": PERSONAL_PROJECT,
            "PERSONAL_GCP_ALLOWED_TABLES": {"personal_ds": frozenset({"base_table", "other_table"})},
            "PERSONAL_GCP_ALLOWED_VIEWS": {"personal_ds": frozenset({"approved_view"})},
            "PERSONAL_GCP_VIEW_DEPENDENCY_CLOSURE": {"personal_ds.approved_view": frozenset({"personal_ds.base_table"})},
        }.items():
            patcher = patch.object(bigquery_dependencies, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def job(self, references, statement_type="SELECT", graphs=()):
        return SimpleNamespace(statement_type=statement_type, referenced_tables=references, referenced_property_graphs=list(graphs))

    def test_actual_dependencies_within_declared_closure_pass(self):
        verify_personal_dry_run(self.SQL, self.job([_reference(PERSONAL_PROJECT, "personal_ds", "base_table")]))
        verify_personal_dry_run(self.SQL, self.job([
            _reference(PERSONAL_PROJECT, "personal_ds", "approved_view"),
            _reference(PERSONAL_PROJECT, "personal_ds", "base_table"),
        ]))

    def test_unverifiable_or_unapproved_dependencies_fail_closed(self):
        class Unavailable:
            statement_type = "SELECT"
            referenced_property_graphs = []

            @property
            def referenced_tables(self):
                raise KeyError("statistics")

        cases = {
            "client project": self.job([_reference("mos-gcp-host-dev-prj", "dw_nsp_api_views", "nsp_base_view_01")]),
            "undeclared approved table": self.job([_reference(PERSONAL_PROJECT, "personal_ds", "other_table")]),
            "unknown table": self.job([_reference(PERSONAL_PROJECT, "personal_ds", "secret_table")]),
            "empty": self.job([]),
            "missing": self.job(None),
            "unavailable": Unavailable(),
            "possibly truncated": self.job([_reference(PERSONAL_PROJECT, "personal_ds", "base_table")] * 50),
            "not select": self.job([_reference(PERSONAL_PROJECT, "personal_ds", "base_table")], statement_type="INSERT"),
            "statement unknown": self.job([_reference(PERSONAL_PROJECT, "personal_ds", "base_table")], statement_type=None),
            "property graph": self.job([_reference(PERSONAL_PROJECT, "personal_ds", "base_table")], graphs=["graph"]),
        }
        for name, job in cases.items():
            with self.subTest(name):
                with self.assertRaises(DependencyVerificationError):
                    verify_personal_dry_run(self.SQL, job)

    def test_sql_outside_personal_allowlists_fails_closed(self):
        job = self.job([_reference(PERSONAL_PROJECT, "personal_ds", "base_table")])
        for sql in (
            "SELECT sport FROM `mos-gcp-host-dev-prj.dw_nsp_api_views.nsp_base_view_01`",
            f"SELECT sport FROM `{PERSONAL_PROJECT}.personal_ds.unknown_view`",
            "DELETE FROM x WHERE TRUE",
        ):
            with self.subTest(sql=sql):
                with self.assertRaises(DependencyVerificationError):
                    verify_personal_dry_run(sql, job)

    def test_personal_dry_run_verifies_before_reserving_usage(self):
        bad_job = self.job([_reference("mos-gcp-host-dev-prj", "dw", "t")])
        bad_job.total_bytes_processed = 10
        client = Mock()
        client.query.return_value = bad_job
        with patch.object(bigquery_database, "RUNTIME_PROFILE", "PERSONAL_GCP"), \
                patch.object(bigquery_database, "require_bigquery_access"), \
                patch.object(bigquery_database, "client", client), \
                patch.object(bigquery_database, "reserve_bigquery_job") as reserve:
            with self.assertRaises(DependencyVerificationError):
                bigquery_database.dry_run_sql(self.SQL)
            reserve.assert_not_called()

            good_job = self.job([_reference(PERSONAL_PROJECT, "personal_ds", "base_table")])
            good_job.total_bytes_processed = 262_144_001
            client.query.return_value = good_job
            with self.assertRaises(bigquery_database.QueryBudgetExceeded):
                bigquery_database.dry_run_sql(self.SQL)
            reserve.assert_not_called()

            good_job.total_bytes_processed = 1024
            self.assertEqual(bigquery_database.dry_run_sql(self.SQL), 1024)
            reserve.assert_called_once_with(1024)


class UsageLimitTests(unittest.TestCase):
    def setUp(self):
        for name, value in {
            "RUNTIME_PROFILE": "PERSONAL_GCP",
            "GEMINI_MAX_CALLS_PER_REQUEST": 2,
            "PERSONAL_GCP_MAX_GEMINI_CALLS_PER_PROCESS": 3,
            "PERSONAL_GCP_MAX_BIGQUERY_JOBS_PER_PROCESS": 2,
            "PERSONAL_GCP_MAX_BIGQUERY_BYTES_PER_PROCESS": 1000,
        }.items():
            patcher = patch.object(usage_limits, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(usage_limits._process_usage, {"gemini_calls": 0, "bigquery_jobs": 0, "bigquery_bytes": 0})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_personal_gemini_calls_require_request_scope_and_respect_limits(self):
        with self.assertRaises(UsageLimitExceeded):
            consume_gemini_call()
        with request_usage_scope():
            consume_gemini_call()
            consume_gemini_call()
            with self.assertRaisesRegex(UsageLimitExceeded, "this request"):
                consume_gemini_call()
        with request_usage_scope():
            consume_gemini_call()
            with self.assertRaisesRegex(UsageLimitExceeded, "this process"):
                consume_gemini_call()

    def test_personal_bigquery_job_and_byte_limits(self):
        reserve_bigquery_job(600)
        with self.assertRaisesRegex(UsageLimitExceeded, "byte limit"):
            reserve_bigquery_job(401)
        reserve_bigquery_job(400)
        with self.assertRaisesRegex(UsageLimitExceeded, "job limit"):
            reserve_bigquery_job(0)
        self.assertEqual(usage_limits.process_usage_snapshot()["bigquery_bytes"], 1000)

    def test_client_profile_bigquery_usage_is_unlimited_by_process_counters(self):
        with patch.object(usage_limits, "RUNTIME_PROFILE", "CLIENT_GCP"):
            for _ in range(5):
                reserve_bigquery_job(10_000)
        self.assertEqual(usage_limits.process_usage_snapshot()["bigquery_jobs"], 0)


class GeminiSafeguardTests(unittest.TestCase):
    def test_lazy_client_applies_timeout_token_cap_and_call_limit(self):
        with patch.object(gemini_service, "GEMINI_MAX_OUTPUT_TOKENS", 256), \
                patch.object(gemini_service, "GEMINI_REQUEST_TIMEOUT_SECONDS", 30), \
                patch.object(gemini_service, "load_gemini_credentials", return_value=None), \
                patch.object(usage_limits, "GEMINI_MAX_CALLS_PER_REQUEST", 3), \
                patch.object(gemini_service.genai, "Client") as client_class:
            lazy = gemini_service._LazyGeminiClient()
            with request_usage_scope():
                lazy.models.generate_content(model="m", contents="c")
                lazy.models.generate_content(model="m", contents="c", config={"max_output_tokens": 10_000})
                lazy.models.generate_content(
                    model="m", contents="c",
                    config=gemini_service.genai_types.GenerateContentConfig(max_output_tokens=100),
                )
                with self.assertRaises(UsageLimitExceeded):
                    lazy.models.generate_content(model="m", contents="c")
        self.assertEqual(client_class.call_args.kwargs["http_options"].timeout, 30_000)
        calls = client_class.return_value.models.generate_content.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].kwargs["config"].max_output_tokens, 256)
        self.assertEqual(calls[1].kwargs["config"]["max_output_tokens"], 256)
        self.assertEqual(calls[2].kwargs["config"].max_output_tokens, 100)

    def test_client_profile_defaults_leave_requests_unchanged(self):
        with patch.object(gemini_service, "load_gemini_credentials", return_value=None), \
                patch.object(gemini_service.genai, "Client") as client_class:
            gemini_service._LazyGeminiClient().models.generate_content(model="m", contents="c")
        self.assertNotIn("http_options", client_class.call_args.kwargs)
        self.assertNotIn("config", client_class.return_value.models.generate_content.call_args.kwargs)


class RequestSafetyMiddlewareTests(unittest.TestCase):
    TOKEN = "t" * 40

    def make_app(self, clock=None, **options):
        application = FastAPI()

        @application.get("/health")
        def health():
            return {"ok": True}

        @application.post("/work")
        def work():
            return {"ok": True}

        @application.get("/exports/{token}")
        def export(token: str):
            return {"token": token}

        @application.post("/stream")
        def stream():
            def body():
                outcomes = []
                for _ in range(3):
                    try:
                        consume_gemini_call()
                        outcomes.append("ok")
                    except UsageLimitExceeded:
                        outcomes.append("limited")
                yield ",".join(outcomes)
            return StreamingResponse(body())

        settings = {"enforce_personal_guard": True, **options}
        if clock is not None:
            settings["clock"] = clock
        application.add_middleware(RequestSafetyMiddleware, **settings)
        return application

    def client(self, application, host="127.0.0.1"):
        return TestClient(application, client=(host, 50000))

    def test_disabled_guard_preserves_existing_access(self):
        application = self.make_app(enforce_personal_guard=False)
        self.assertEqual(self.client(application, "203.0.113.10").post("/work").status_code, 200)

    def test_personal_guard_is_localhost_only_by_default(self):
        application = self.make_app()
        self.assertEqual(self.client(application).post("/work").status_code, 200)
        self.assertEqual(self.client(application, "::1").get("/health").status_code, 200)
        for host in ("203.0.113.10", "10.0.0.5", "testclient", "localhost.evil"):
            with self.subTest(host=host):
                self.assertEqual(self.client(application, host).post("/work").status_code, 403)
        self.assertNotEqual(self.client(application, "203.0.113.10").options("/work").status_code, 403)

    def test_token_is_required_when_configured(self):
        application = self.make_app(api_token=self.TOKEN)
        local = self.client(application)
        self.assertEqual(local.post("/work").status_code, 401)
        self.assertEqual(local.post("/work", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(local.post("/work", headers={"Authorization": f"Bearer {self.TOKEN}"}).status_code, 200)

    def test_remote_access_requires_explicit_opt_in_and_token(self):
        with self.assertRaises(ValueError):
            RequestSafetyMiddleware(FastAPI(), enforce_personal_guard=True, allow_remote_clients=True)
        application = self.make_app(allow_remote_clients=True, api_token=self.TOKEN)
        remote = self.client(application, "203.0.113.10")
        self.assertEqual(remote.post("/work").status_code, 401)
        self.assertEqual(remote.post("/work", headers={"Authorization": f"Bearer {self.TOKEN}"}).status_code, 200)

    def test_cost_bearing_requests_are_rate_limited(self):
        now = [1000.0]
        application = self.make_app(rate_limit_per_minute=2, clock=lambda: now[0])
        local = self.client(application)
        self.assertEqual([local.post("/work").status_code for _ in range(3)], [200, 200, 429])
        self.assertEqual(local.get("/exports/abc").status_code, 429)
        self.assertEqual(local.get("/health").status_code, 200)
        now[0] += 61
        self.assertEqual(local.get("/exports/abc").status_code, 200)

    def test_request_scope_reaches_streaming_threadpool_bodies(self):
        application = self.make_app()
        with patch.object(usage_limits, "RUNTIME_PROFILE", "PERSONAL_GCP"), \
                patch.object(usage_limits, "GEMINI_MAX_CALLS_PER_REQUEST", 2), \
                patch.object(usage_limits, "PERSONAL_GCP_MAX_GEMINI_CALLS_PER_PROCESS", 100), \
                patch.dict(usage_limits._process_usage, {"gemini_calls": 0}):
            local = self.client(application)
            self.assertEqual(local.post("/stream").text, "ok,ok,limited")
            self.assertEqual(local.post("/stream").text, "ok,ok,limited")


class ProductionAppMiddlewareTests(unittest.TestCase):
    def test_client_profile_app_does_not_enforce_personal_guard(self):
        import app

        guards = [m for m in app.app.user_middleware if m.cls is RequestSafetyMiddleware]
        self.assertEqual(len(guards), 1)
        self.assertFalse(guards[0].kwargs["enforce_personal_guard"])
        # CORS must stay outermost so rejected responses still carry CORS headers.
        self.assertEqual(app.app.user_middleware[0].cls.__name__, "CORSMiddleware")


if __name__ == "__main__":
    unittest.main()
