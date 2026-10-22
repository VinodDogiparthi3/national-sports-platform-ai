"""Per-request and per-process usage limits for Gemini and BigQuery.

These counters live in process memory. They reset on restart and are not shared between
workers or instances, so they are not durable spending caps; use GCP budgets and BigQuery
custom quotas for that.
"""

import threading
from contextlib import contextmanager
from contextvars import ContextVar

from config import (
    GEMINI_MAX_CALLS_PER_REQUEST,
    PERSONAL_GCP_MAX_BIGQUERY_BYTES_PER_PROCESS,
    PERSONAL_GCP_MAX_BIGQUERY_JOBS_PER_PROCESS,
    PERSONAL_GCP_MAX_GEMINI_CALLS_PER_PROCESS,
    RUNTIME_PROFILE,
)


class UsageLimitExceeded(RuntimeError):
    """Raised when a configured usage limit would be exceeded."""


class _RequestUsage:
    def __init__(self):
        self.gemini_calls = 0


# Holds a mutable object so copied contexts (threadpools, streaming bodies) share one counter.
_request_usage: ContextVar[_RequestUsage | None] = ContextVar("request_usage", default=None)
_lock = threading.Lock()
_process_usage = {"gemini_calls": 0, "bigquery_jobs": 0, "bigquery_bytes": 0}


@contextmanager
def request_usage_scope():
    token = _request_usage.set(_RequestUsage())
    try:
        yield
    finally:
        _request_usage.reset(token)


def consume_gemini_call() -> None:
    personal = RUNTIME_PROFILE == "PERSONAL_GCP"
    with _lock:
        usage = _request_usage.get()
        if personal and usage is None:
            raise UsageLimitExceeded("PERSONAL_GCP Gemini calls are allowed only within an API request.")
        if (
            usage is not None
            and GEMINI_MAX_CALLS_PER_REQUEST is not None
            and usage.gemini_calls >= GEMINI_MAX_CALLS_PER_REQUEST
        ):
            raise UsageLimitExceeded("The Gemini call limit for this request has been reached.")
        if personal and _process_usage["gemini_calls"] >= PERSONAL_GCP_MAX_GEMINI_CALLS_PER_PROCESS:
            raise UsageLimitExceeded("The PERSONAL_GCP Gemini call limit for this process has been reached.")
        if usage is not None:
            usage.gemini_calls += 1
        _process_usage["gemini_calls"] += 1


def reserve_bigquery_job(estimated_bytes: int) -> None:
    if RUNTIME_PROFILE != "PERSONAL_GCP":
        return
    if estimated_bytes < 0:
        raise UsageLimitExceeded("BigQuery byte estimate is invalid.")
    with _lock:
        if _process_usage["bigquery_jobs"] >= PERSONAL_GCP_MAX_BIGQUERY_JOBS_PER_PROCESS:
            raise UsageLimitExceeded("The PERSONAL_GCP BigQuery job limit for this process has been reached.")
        if _process_usage["bigquery_bytes"] + estimated_bytes > PERSONAL_GCP_MAX_BIGQUERY_BYTES_PER_PROCESS:
            raise UsageLimitExceeded("The PERSONAL_GCP BigQuery byte limit for this process has been reached.")
        _process_usage["bigquery_jobs"] += 1
        _process_usage["bigquery_bytes"] += estimated_bytes


def process_usage_snapshot() -> dict[str, int]:
    with _lock:
        return dict(_process_usage)
