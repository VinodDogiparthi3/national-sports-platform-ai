import os


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
MAX_BYTES_PROCESSED = int(
    os.getenv(
        "BIGQUERY_MAX_BYTES_PROCESSED",
        str(250 * 1024 * 1024),
    )
)
QUERY_TIMEOUT_SECONDS = int(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "60"))
SCHEMA_CACHE_TTL_SECONDS = int(os.getenv("SCHEMA_CACHE_TTL_SECONDS", "3600"))


# -------------------------
# Excel export
# -------------------------

EXPORT_MAX_ROWS = int(os.getenv("EXPORT_MAX_ROWS", "500000"))
EXPORT_CONTEXT_TTL_SECONDS = int(os.getenv("EXPORT_CONTEXT_TTL_SECONDS", str(24 * 3600)))
EXPORT_DOWNLOAD_TTL_SECONDS = int(os.getenv("EXPORT_DOWNLOAD_TTL_SECONDS", "3600"))
# Must be identical on every Cloud Run instance, otherwise tokens only work on the issuing instance.
EXPORT_SECRET_KEY = os.getenv("EXPORT_SECRET_KEY")