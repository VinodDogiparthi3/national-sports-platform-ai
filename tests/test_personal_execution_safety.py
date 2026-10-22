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

    def test_personal_defaults_are_bounded_and_execution_stays_disabled(self):
        result = run_python(
            "from unittest.mock import patch; import config; "
            "assert config.PERSONAL_GCP_EXECUTION_ENABLED is False; "
            "assert config.MAX_BYTES_PROCESSED == 262144000; "
            "assert config.BIGQUERY_JOB_LOCATION == 'US'; "
            "patcher = patch.object(config, 'load_bigquery_credentials', return_value=None); patcher.start(); "
            "assert config.get_bigquery_client_kwargs() == {'project': config.PERSONAL_GCP_PROJECT_ID, 'location': 'US'}; "
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
            ({"PERSONAL_GCP_EXECUTION_ENABLED": "true"}, "cannot be enabled in this checkpoint"),
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
