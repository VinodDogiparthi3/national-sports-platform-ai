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


# -------------------------
# PostgreSQL
# -------------------------

DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "national_sports_poc")
DB_USER = os.getenv("POSTGRES_USER", "postgres")


def get_db_password() -> str:
    password = os.getenv("POSTGRES_PASSWORD")

    if not password:
        raise RuntimeError(
            "POSTGRES_PASSWORD environment variable is not set."
        )

    return password