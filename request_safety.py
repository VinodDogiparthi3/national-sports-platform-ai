"""ASGI middleware: per-request usage scope plus PERSONAL_GCP access and rate-limit guards."""

import hmac
import ipaddress
import threading
import time
from collections import deque

from starlette.responses import JSONResponse

from usage_limits import request_usage_scope

RATE_LIMIT_WINDOW_SECONDS = 60.0
MAX_TRACKED_CLIENTS = 10_000


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_cost_bearing(scope) -> bool:
    return scope["method"] == "POST" or scope["path"].startswith("/exports/")


class RequestSafetyMiddleware:
    def __init__(
        self,
        app,
        *,
        enforce_personal_guard: bool,
        allow_remote_clients: bool = False,
        api_token: str | None = None,
        rate_limit_per_minute: int | None = None,
        clock=time.monotonic,
    ):
        if enforce_personal_guard and allow_remote_clients and not api_token:
            raise ValueError("Remote PERSONAL_GCP clients require an API token.")
        self.app = app
        self.enforce_personal_guard = enforce_personal_guard
        self.allow_remote_clients = allow_remote_clients
        self.api_token = api_token
        self.rate_limit_per_minute = rate_limit_per_minute
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.enforce_personal_guard and scope["method"] != "OPTIONS":
            rejection = self._reject(scope)
            if rejection is not None:
                status_code, detail = rejection
                await JSONResponse({"detail": detail}, status_code=status_code)(scope, receive, send)
                return
        with request_usage_scope():
            await self.app(scope, receive, send)

    def _reject(self, scope) -> tuple[int, str] | None:
        host = (scope.get("client") or (None, None))[0]
        if not self.allow_remote_clients and not _is_loopback(host):
            return 403, "PERSONAL_GCP accepts requests from localhost only."
        if self.api_token and not self._authorized(scope):
            return 401, "A valid PERSONAL_GCP API token is required."
        if self.rate_limit_per_minute and _is_cost_bearing(scope) and not self._allow_rate(host or "unknown"):
            return 429, "PERSONAL_GCP request rate limit exceeded. Please wait and try again."
        return None

    def _authorized(self, scope) -> bool:
        headers = dict(scope.get("headers") or ())
        value = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, token = value.partition(" ")
        return scheme.casefold() == "bearer" and hmac.compare_digest(token.strip().encode(), self.api_token.encode())

    def _allow_rate(self, client_key: str) -> bool:
        now = self.clock()
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        with self._lock:
            if client_key not in self._hits and len(self._hits) >= MAX_TRACKED_CLIENTS:
                for key in [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
                    del self._hits[key]
                if len(self._hits) >= MAX_TRACKED_CLIENTS:
                    return False
            hits = self._hits.setdefault(client_key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.rate_limit_per_minute:
                return False
            hits.append(now)
            return True
