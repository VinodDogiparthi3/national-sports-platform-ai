import os


# -------------------------
# Vertex AI / Gemini
# -------------------------

PROJECT_ID = "mos-gcp-host-dev-prj"
LOCATION = "global"
MODEL = "gemini-3.8-flash"


# -------------------------
# PostgreSQL
# -------------------------

DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "national_sports_poc"
DB_USER = "postgres"


def get_db_password() -> str:
    password = os.getenv("POSTGRES_PASSWORD")

    if not password:
        raise RuntimeError(
            "POSTGRES_PASSWORD environment variable is not set."
        )

    return password