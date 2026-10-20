import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from uc2.security import (
    InvalidCredentials,
    VerifiedPrincipal,
    get_authentication_verifier,
    get_repository,
)


class VerifierDouble:
    def __init__(self):
        self.tokens = []
        self.principal = VerifiedPrincipal(subject="synthetic-test-reader", permissions={"uc2:read"})
        self.error = None

    def verify(self, token):
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        return self.principal


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.verifier = VerifierDouble()
        self.app.dependency_overrides[get_authentication_verifier] = lambda: self.verifier

        @self.app.get("/probe")
        def probe(repository=Depends(get_repository)):
            return {"fixture_version": repository.fixture_version}

        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_missing_credentials_never_reach_verifier_or_repository(self):
        with patch("uc2.security.SyntheticRepository") as repository:
            response = self.client.get("/probe")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.verifier.tokens, [])
        repository.assert_not_called()

    def test_invalid_bearer_headers_are_rejected_before_repository(self):
        for value in ("", "Basic opaque-test-token", "Bearer", "Bearer ", "Bearer two tokens"):
            with self.subTest(value=value), patch("uc2.security.SyntheticRepository") as repository:
                response = self.client.get("/probe", headers={"Authorization": value})
                self.assertEqual(response.status_code, 401)
                repository.assert_not_called()
        self.assertEqual(self.verifier.tokens, [])

    def test_duplicate_authorization_headers_are_rejected(self):
        with patch("uc2.security.SyntheticRepository") as repository:
            response = self.client.get("/probe", headers=[("Authorization", "Bearer first"), ("Authorization", "Bearer second")])
        self.assertEqual(response.status_code, 401)
        repository.assert_not_called()

    def test_verifier_rejection_is_unauthenticated(self):
        self.verifier.error = InvalidCredentials("private verifier detail")
        with patch("uc2.security.SyntheticRepository") as repository:
            response = self.client.get("/probe", headers={"Authorization": "Bearer invalid-test-token"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("private verifier detail", response.text)
        repository.assert_not_called()

    def test_missing_permission_and_forged_roles_cannot_access_repository(self):
        self.verifier.principal = VerifiedPrincipal(subject="test-reader", permissions={"uc1:read"})
        with patch("uc2.security.SyntheticRepository") as repository:
            response = self.client.get("/probe", headers={
                "Authorization": "Bearer unauthorized-test-token", "X-Role": "admin",
                "X-Permissions": "uc2:read", "X-User": "privileged-user",
            })
        self.assertEqual(response.status_code, 403)
        repository.assert_not_called()

    def test_role_headers_alone_are_not_authentication(self):
        with patch("uc2.security.SyntheticRepository") as repository:
            response = self.client.get("/probe", headers={"X-Role": "admin", "X-Permissions": "uc2:read"})
        self.assertEqual(response.status_code, 401)
        repository.assert_not_called()

    def test_valid_verified_identity_can_acquire_synthetic_repository(self):
        response = self.client.get("/probe", headers={"Authorization": "Bearer opaque-test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["fixture_version"], "synthetic-workflows-v1")
        self.assertEqual(self.verifier.tokens, ["opaque-test-token"])

    def test_unconfigured_verifier_is_fail_closed(self):
        self.app.dependency_overrides.clear()
        with patch("uc2.security.SyntheticRepository") as repository:
            response = self.client.get("/probe", headers={"Authorization": "Bearer opaque-test-token"})
        self.assertEqual(response.status_code, 500)
        repository.assert_not_called()

    def test_invalid_principal_and_verifier_failures_are_fail_closed(self):
        for value in (None, {"subject": "forged", "permissions": ["uc2:read"]}):
            self.verifier.principal = value
            with self.subTest(value=value), patch("uc2.security.SyntheticRepository") as repository:
                response = self.client.get("/probe", headers={"Authorization": "Bearer opaque-test-token"})
                self.assertEqual(response.status_code, 500)
                repository.assert_not_called()
        self.verifier.error = RuntimeError("private upstream failure")
        with patch("uc2.security.SyntheticRepository") as repository:
            response = self.client.get("/probe", headers={"Authorization": "Bearer opaque-test-token"})
        self.assertEqual(response.status_code, 500)
        repository.assert_not_called()