"""Central fail-closed guard for BigQuery access when schemas are local-only."""

from config import PERSONAL_GCP_EXECUTION_ENABLED, SCHEMA_PROVIDER


class BigQueryAccessDisabledError(RuntimeError):
    """Raised when a BigQuery operation is attempted in LOCAL schema mode."""


class GeminiAccessDisabledError(RuntimeError):
    """Raised when Gemini inference is disabled for the selected runtime profile."""


def require_bigquery_access(operation: str) -> None:
    if SCHEMA_PROVIDER == "LOCAL":
        raise BigQueryAccessDisabledError(
            f"BigQuery {operation} is disabled when SCHEMA_PROVIDER=LOCAL."
        )
    if SCHEMA_PROVIDER == "PERSONAL_GCP" and not PERSONAL_GCP_EXECUTION_ENABLED:
        raise BigQueryAccessDisabledError(
            f"BigQuery {operation} is disabled for PERSONAL_GCP in this checkpoint."
        )


def require_gemini_access(operation: str) -> None:
    if SCHEMA_PROVIDER == "PERSONAL_GCP" and not PERSONAL_GCP_EXECUTION_ENABLED:
        raise GeminiAccessDisabledError(
            f"Gemini {operation} is disabled for PERSONAL_GCP in this checkpoint."
        )