import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from uc2.models import Application, SyntheticDataset
from uc2.repository import SyntheticRepository
from uc2.router import router
from uc2.security import AuthenticationVerifier, InvalidCredentials, VerifiedPrincipal, get_authentication_verifier


AS_OF = "2026-11-01T00:00:00Z"
SUMMARY = "/api/uc2/license-delays"
ANALYSIS = "/api/uc2/applications/SYN-D/analysis"


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden"))
        self.dns = patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden"))
        self.connect = self.network.start()
        self.lookup = self.dns.start()
        self.addCleanup(self.network.stop)
        self.addCleanup(self.dns.stop)
        self.app = FastAPI()
        self.app.include_router(router)
        self.verifier = Mock(spec=AuthenticationVerifier)
        self.verifier.verify.return_value = VerifiedPrincipal(subject="synthetic-test-reader", permissions={"uc2:read"})
        self.app.dependency_overrides[get_authentication_verifier] = lambda: self.verifier
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {"Authorization": "Bearer opaque-test-token"}

    def tearDown(self):
        self.connect.assert_not_called()
        self.lookup.assert_not_called()

    def get(self, path=SUMMARY, params=None, headers=None):
        return self.client.get(path, params=params if params is not None else {"received_month": "2026-10", "as_of": AS_OF}, headers=self.headers if headers is None else headers)

    def assert_error(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["data_mode"], "synthetic")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("code", response.json()["error"])
        self.assertIn("message", response.json()["error"])
        if status == 401:
            self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")

    def test_summary_preserves_scenario_and_existing_response_contract(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["data_mode"], "synthetic")
        self.assertEqual(body["fixture_version"], "synthetic-workflows-v1")
        self.assertEqual(body["policy_version"], "synthetic-calendar-v1")
        self.assertEqual(body["cohort_median_hours"], 66)
        self.assertEqual(body["outlier_threshold_hours"], 198)
        self.assertEqual(body["breached_count"], 1)
        self.assertEqual([item["application_id"] for item in body["applications"]], ["SYN-A", "SYN-B", "SYN-C", "SYN-D"])
        self.assertEqual([item["elapsed_hours"] for item in body["applications"]], [48, 72, 60, 600])
        self.assertEqual([item["outlier_status"] for item in body["applications"]], ["not_outlier"] * 3 + ["outlier"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.verifier.verify.assert_called_once_with("opaque-test-token")

    def test_analysis_preserves_evidence_and_supported_findings(self):
        response = self.get(ANALYSIS, {"as_of": AS_OF})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["data_mode"], "synthetic")
        self.assertEqual((body["elapsed_hours"], body["sla_excess_hours"]), (600, 504))
        self.assertEqual(body["responsible_stage"], "approval")
        self.assertEqual(body["cause_status"], "documented_blocker")
        self.assertEqual(body["stage_visits"][2]["supported_blocked_hours"], 552)
        evidence_ids = {item["evidence_id"] for item in body["evidence"]}
        self.assertTrue(all(item["synthetic"] for item in body["evidence"]))
        self.assertTrue(all(set(item["evidence_ids"]) <= evidence_ids for item in body["findings"] + body["recommendations"]))
        self.assertEqual(body["hypotheses"], [])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_each_individual_application_matches_summary(self):
        for identifier, expected in (("A", 48), ("B", 72), ("C", 60), ("D", 600)):
            with self.subTest(identifier=identifier):
                response = self.get(f"/api/uc2/applications/SYN-{identifier}/analysis", {"as_of": AS_OF})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["elapsed_hours"], expected)

    def test_query_validation_rejects_missing_naive_invalid_extra_and_duplicate_values(self):
        invalid = [
            {}, {"received_month": "2026-10"}, {"as_of": AS_OF},
            {"received_month": "2026-13", "as_of": AS_OF},
            {"received_month": "0000-10", "as_of": AS_OF},
            {"received_month": "2026-1", "as_of": AS_OF},
            {"received_month": "2026-10", "as_of": "2026-11-01T00:00:00"},
            {"received_month": "2026-10", "as_of": "2026-02-30T00:00:00Z"},
            {"received_month": "2026-10", "as_of": "1793491200"},
            {"received_month": "2026-10", "as_of": AS_OF, "dataset": "private"},
            [("received_month", "2026-10"), ("as_of", AS_OF), ("as_of", AS_OF)],
        ]
        for params in invalid:
            with self.subTest(params=params):
                self.assert_error(self.get(params=params), 422)

    def test_analysis_query_and_path_validation(self):
        for params in ({}, {"as_of": "not-a-date"}, {"as_of": AS_OF, "received_month": "2026-10"}):
            with self.subTest(params=params):
                self.assert_error(self.get(ANALYSIS, params), 422)
        self.assert_error(self.get("/api/uc2/applications/has%20space/analysis", {"as_of": AS_OF}), 422)
        self.assert_error(self.get("/api/uc2/applications/" + "X" * 201 + "/analysis", {"as_of": AS_OF}), 422)

    def test_timezone_offsets_normalize_to_utc(self):
        response = self.get(ANALYSIS, {"as_of": "2026-11-01T03:00:00+03:00"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["as_of"], AS_OF)

    def test_unknown_or_not_yet_received_application_is_404(self):
        self.assert_error(self.get("/api/uc2/applications/SYN-UNKNOWN/analysis", {"as_of": AS_OF}), 404)
        self.assert_error(self.get(ANALYSIS, {"as_of": "2026-09-01T00:00:00Z"}), 404)

    def test_empty_cohort_is_success(self):
        response = self.get(params={"received_month": "2026-09", "as_of": AS_OF})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["applications"], [])

    def test_unauthenticated_invalid_and_unauthorized_requests_never_acquire_repository(self):
        for path, params in ((SUMMARY, {"received_month": "2026-10", "as_of": AS_OF}), (ANALYSIS, {"as_of": AS_OF})):
            with self.subTest(path=path), patch("uc2.security.SyntheticRepository") as repository:
                self.assert_error(self.get(path, params, {}), 401)
                self.verifier.verify.side_effect = InvalidCredentials("private reason")
                self.assert_error(self.get(path, params), 401)
                self.verifier.verify.side_effect = None
                self.verifier.verify.return_value = VerifiedPrincipal(subject="test-reader")
                self.assert_error(self.get(path, params, {**self.headers, "X-Role": "admin", "X-Permissions": "uc2:read"}), 403)
                self.verifier.verify.return_value = VerifiedPrincipal(subject="test-reader", permissions={"uc2:read"})
                repository.assert_not_called()

    def test_invalid_queries_do_not_bypass_authentication(self):
        with patch("uc2.security.SyntheticRepository") as repository:
            self.assert_error(self.get(params={}, headers={}), 401)
            self.assert_error(self.get(params=[("as_of", AS_OF), ("as_of", AS_OF)], headers={}), 401)
        repository.assert_not_called()

    def test_unconfigured_or_failing_verifier_returns_sanitized_500(self):
        self.app.dependency_overrides.clear()
        with patch("uc2.security.SyntheticRepository") as repository:
            response = self.get()
        self.assert_error(response, 500)
        self.assertNotIn("configured", response.text)
        repository.assert_not_called()
        self.app.dependency_overrides[get_authentication_verifier] = lambda: self.verifier
        self.verifier.verify.side_effect = RuntimeError("SECRET_CREDENTIAL_AND_UPSTREAM_FAILURE")
        with patch("uc2.security.SyntheticRepository") as repository:
            response = self.get()
        self.assert_error(response, 500)
        self.assertNotIn("SECRET_CREDENTIAL_AND_UPSTREAM_FAILURE", response.text)
        repository.assert_not_called()

    def test_internal_repository_and_response_errors_are_sanitized(self):
        with patch("uc2.security.SyntheticRepository", side_effect=RuntimeError("SECRET_FIXTURE_PATH")):
            response = self.get()
        self.assert_error(response, 500)
        self.assertNotIn("SECRET_FIXTURE_PATH", response.text)
        with patch("uc2.router.summarize", return_value={"invalid": "SECRET_RESPONSE"}):
            response = self.get()
        self.assert_error(response, 500)
        self.assertNotIn("SECRET_RESPONSE", response.text)
        with patch("uc2.router.analyze", side_effect=KeyError("PRIVATE_INTERNAL_KEY")):
            response = self.get(ANALYSIS, {"as_of": AS_OF})
        self.assert_error(response, 500)
        self.assertNotIn("PRIVATE_INTERNAL_KEY", response.text)

    def test_partial_and_insufficient_evidence_remain_successful_analyses(self):
        original = SyntheticRepository()
        data = original.get_application("SYN-D", AS_OF).model_dump()
        data["stage_visits"][2]["blocking_intervals"][0]["evidence_ids"] = ()
        repository = SyntheticRepository(SyntheticDataset(
            fixture_version="synthetic-partial-test", assumptions=("Fictional evidence gaps.",),
            policy=original.policy, applications=(Application.model_validate(data),),
        ))
        with patch("uc2.security.SyntheticRepository", return_value=repository):
            response = self.get(ANALYSIS, {"as_of": AS_OF})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["analysis_status"], "partial")
        self.assertEqual(body["cause_status"], "insufficient_evidence")
        self.assertTrue(body["data_quality_issues"])
        self.assertEqual(body["hypotheses"], [])

    def test_cutoff_does_not_leak_future_evidence(self):
        response = self.get(ANALYSIS, {"as_of": "2026-10-03T09:00:00Z"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "open")
        self.assertNotIn("SYN-D-VERIFY-BLOCK-END", [item["evidence_id"] for item in body["evidence"]])

    def test_openapi_declares_existing_models_and_synthetic_errors(self):
        schema = self.app.openapi()
        summary = schema["paths"][SUMMARY]["get"]
        self.assertEqual(summary["responses"]["200"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/SummaryResponse")
        self.assertTrue({"401", "403", "404", "422", "500"} <= summary["responses"].keys())

    def test_router_import_and_test_mount_are_independent_of_uc1_and_network(self):
        script = """
import socket
import sys
from unittest.mock import patch
with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')) as connect:
    with patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')) as dns:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from uc2.router import router
        application = FastAPI()
        application.include_router(router)
        response = TestClient(application).get('/api/uc2/license-delays')
        assert response.status_code == 401
        assert response.json()['data_mode'] == 'synthetic'
        connect.assert_not_called()
        dns.assert_not_called()
        forbidden = {'app', 'config', 'bigquery_database', 'gemini_service', 'view_catalog'}
        assert not forbidden.intersection(sys.modules)
        assert not any(name.startswith('google.cloud') or name.startswith('google.genai') for name in sys.modules)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)