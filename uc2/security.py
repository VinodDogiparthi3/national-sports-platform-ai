from typing import Annotated, Protocol

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from uc2.repository import SyntheticRepository, WorkflowRepository


UC2_READ_PERMISSION = "uc2:read"


class VerifiedPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1, max_length=200, pattern=r"^\S+$")
    permissions: frozenset[str] = frozenset()


class InvalidCredentials(Exception):
    pass


class AuthenticationVerifier(Protocol):
    def verify(self, bearer_token: str) -> VerifiedPrincipal: ...


def bearer_token(request: Request) -> str:
    headers = request.headers.getlist("authorization")
    if len(headers) != 1:
        raise HTTPException(status_code=401, detail="Bearer authentication required.")
    scheme, separator, token = headers[0].partition(" ")
    if scheme.casefold() != "bearer" or not separator or not token or any(character.isspace() for character in token):
        raise HTTPException(status_code=401, detail="Invalid bearer credentials.")
    return token


def get_authentication_verifier() -> AuthenticationVerifier:
    raise RuntimeError("UC2 requires an approved authentication verifier; none is configured.")


def authenticate(
    token: Annotated[str, Depends(bearer_token)],
    verifier: Annotated[AuthenticationVerifier, Depends(get_authentication_verifier)],
) -> VerifiedPrincipal:
    try:
        principal = verifier.verify(token)
    except InvalidCredentials:
        raise HTTPException(status_code=401, detail="Invalid bearer credentials.") from None
    if not isinstance(principal, VerifiedPrincipal):
        raise RuntimeError("Authentication verifier did not return a verified principal.")
    return principal


def require_uc2_access(
    principal: Annotated[VerifiedPrincipal, Depends(authenticate)],
) -> VerifiedPrincipal:
    if UC2_READ_PERMISSION not in principal.permissions:
        raise HTTPException(status_code=403, detail="UC2 read permission required.")
    return principal


def get_repository(
    principal: Annotated[VerifiedPrincipal, Depends(require_uc2_access)],
) -> WorkflowRepository:
    return SyntheticRepository()