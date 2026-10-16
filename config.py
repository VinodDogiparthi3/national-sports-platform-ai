import os
import json


# -------------------------
# Vertex AI / Gemini
# -------------------------

PROJECT_ID = os.getenv(
    "GOOGLE_CLOUD_PROJECT",
    "mos-gcp-host-dev-prj",
)
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


# -------------------------
# BigQuery
# -------------------------

BIGQUERY_PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID", PROJECT_ID)
BIGQUERY_DATASET_ID = os.getenv("BIGQUERY_DATASET_ID", "dw_nsp_api_views")
BIGQUERY_TABLE_ID = os.getenv("BIGQUERY_TABLE_ID", "nsp_base_view_01")
HARD_MAX_BYTES_PROCESSED = 262_144_000


def bounded_max_bytes(value: str | int | None) -> int:
    """Allow a lower operational cap, but never permit configuration above 250 MiB."""
    requested = HARD_MAX_BYTES_PROCESSED if value is None else int(value)
    if requested <= 0:
        raise ValueError("BIGQUERY_MAX_BYTES_PROCESSED must be positive.")
    return min(requested, HARD_MAX_BYTES_PROCESSED)


MAX_BYTES_PROCESSED = bounded_max_bytes(os.getenv("BIGQUERY_MAX_BYTES_PROCESSED"))
QUERY_TIMEOUT_SECONDS = int(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "60"))
SCHEMA_CACHE_TTL_SECONDS = int(os.getenv("SCHEMA_CACHE_TTL_SECONDS", "3600"))
BIGQUERY_CATALOG_CACHE_TTL_SECONDS = int(os.getenv("BIGQUERY_CATALOG_CACHE_TTL_SECONDS", "300"))
BIGQUERY_DYNAMIC_CATALOG_ENABLED = os.getenv("BIGQUERY_DYNAMIC_CATALOG_ENABLED", "true").casefold() in {"1", "true", "yes"}
BIGQUERY_DYNAMIC_ROUTING_ENABLED = os.getenv("BIGQUERY_DYNAMIC_ROUTING_ENABLED", "true").casefold() in {"1", "true", "yes"}
BIGQUERY_DYNAMIC_ALLOWED_DATASETS = frozenset(
    dataset.strip().casefold()
    for dataset in os.getenv("BIGQUERY_DYNAMIC_ALLOWED_DATASETS", "").split(",")
    if dataset.strip()
)

try:
    BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES = json.loads(
        os.getenv("BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES", "{}")
    )
except json.JSONDecodeError as error:
    raise ValueError("BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES must be valid JSON.") from error

if (
    not isinstance(BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES, dict)
    or any(
        not isinstance(source_id, str)
        or not isinstance(columns, list)
        or not all(isinstance(column, str) for column in columns)
        for source_id, columns in BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES.items()
    )
):
    raise ValueError("Dynamic source policies must map source IDs to lists of column names.")


# -------------------------
# Excel export
# -------------------------

EXPORT_MAX_ROWS = int(os.getenv("EXPORT_MAX_ROWS", "500000"))
EXPORT_CONTEXT_TTL_SECONDS = int(os.getenv("EXPORT_CONTEXT_TTL_SECONDS", str(24 * 3600)))
EXPORT_DOWNLOAD_TTL_SECONDS = int(os.getenv("EXPORT_DOWNLOAD_TTL_SECONDS", "3600"))
# Must be identical on every Cloud Run instance, otherwise tokens only work on the issuing instance.
EXPORT_SECRET_KEY = os.getenv("EXPORT_SECRET_KEY")