import os
import json
from pathlib import Path
import re


RUNTIME_PROFILE_OPTIONS = ("CLIENT_GCP", "PERSONAL_GCP", "LOCAL")
_configured_profile = os.getenv("RUNTIME_PROFILE")
_legacy_schema_provider = os.getenv("SCHEMA_PROVIDER")
if _configured_profile and _legacy_schema_provider and _configured_profile != _legacy_schema_provider:
    raise ValueError("RUNTIME_PROFILE conflicts with the legacy SCHEMA_PROVIDER setting.")
if _configured_profile is not None:
    RUNTIME_PROFILE = _configured_profile
elif _legacy_schema_provider is not None:
    RUNTIME_PROFILE = _legacy_schema_provider
else:
    RUNTIME_PROFILE = "CLIENT_GCP"
if RUNTIME_PROFILE not in RUNTIME_PROFILE_OPTIONS:
    if _configured_profile is None and _legacy_schema_provider is not None:
        raise ValueError("SCHEMA_PROVIDER must be exactly CLIENT_GCP, PERSONAL_GCP, or LOCAL.")
    raise ValueError("RUNTIME_PROFILE must be exactly CLIENT_GCP, PERSONAL_GCP, or LOCAL.")
SCHEMA_PROVIDER = RUNTIME_PROFILE

CLIENT_GCP_PROJECT_ID = os.getenv("CLIENT_GCP_PROJECT_ID")
if not CLIENT_GCP_PROJECT_ID:
    CLIENT_GCP_PROJECT_ID = (
        os.getenv("GOOGLE_CLOUD_PROJECT", "mos-gcp-host-dev-prj")
        if RUNTIME_PROFILE == "CLIENT_GCP"
        else "mos-gcp-host-dev-prj"
    )


def _required_profile_value(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} is required when RUNTIME_PROFILE=PERSONAL_GCP.")
    return value.strip()


def _optional_credentials_file(name: str, *, required: bool = False) -> str | None:
    raw_path = os.getenv(name)
    if raw_path is None or not raw_path.strip():
        if required:
            raise ValueError(f"{name} is required when RUNTIME_PROFILE=PERSONAL_GCP.")
        return None
    path = Path(raw_path.strip()).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{name} must point to an existing absolute credential file.")
    resolved_path = path.resolve()
    repository_root = Path(__file__).resolve().parent
    if resolved_path == repository_root or repository_root in resolved_path.parents:
        raise ValueError(f"{name} must not point inside the source repository.")
    return str(resolved_path)


PERSONAL_GCP_EXECUTION_ENABLED = False
PERSONAL_GCP_ALLOWED_DATASETS: frozenset[str] = frozenset()
PERSONAL_GCP_PROJECT_ID: str | None = None
PERSONAL_GEMINI_PROJECT_ID: str | None = None
PERSONAL_BIGQUERY_PROJECT_ID: str | None = None
PERSONAL_GCP_DEFAULT_DATASET_ID: str | None = None
PERSONAL_GCP_DEFAULT_TABLE_ID: str | None = None
PERSONAL_GCP_GEMINI_LOCATION: str | None = None
PERSONAL_GCP_BIGQUERY_LOCATION: str | None = None
GEMINI_CREDENTIALS_FILE: str | None = None
BIGQUERY_CREDENTIALS_FILE: str | None = None


# -------------------------
# Vertex AI / Gemini
# -------------------------

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


# -------------------------
# BigQuery
# -------------------------

_location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
_legacy_gemini_project = os.getenv("GEMINI_PROJECT_ID")
if _legacy_gemini_project is not None:
    _legacy_gemini_project = _legacy_gemini_project.strip() or None

if RUNTIME_PROFILE == "PERSONAL_GCP":
    if os.getenv("PERSONAL_GCP_EXECUTION_ENABLED", "false").casefold() in {"1", "true", "yes"}:
        raise ValueError("PERSONAL_GCP execution cannot be enabled in this checkpoint.")
    PERSONAL_GCP_PROJECT_ID = _required_profile_value("PERSONAL_GCP_PROJECT_ID")
    PERSONAL_GEMINI_PROJECT_ID = _required_profile_value("PERSONAL_GEMINI_PROJECT_ID")
    PERSONAL_BIGQUERY_PROJECT_ID = _required_profile_value("PERSONAL_BIGQUERY_PROJECT_ID")
    if any(project_id.casefold() == CLIENT_GCP_PROJECT_ID.casefold() for project_id in (
        PERSONAL_GCP_PROJECT_ID, PERSONAL_GEMINI_PROJECT_ID, PERSONAL_BIGQUERY_PROJECT_ID,
    )):
        raise ValueError("PERSONAL_GCP project IDs must not equal CLIENT_GCP_PROJECT_ID.")
    if any(project_id.casefold() != PERSONAL_GCP_PROJECT_ID.casefold() for project_id in (
        PERSONAL_GEMINI_PROJECT_ID, PERSONAL_BIGQUERY_PROJECT_ID,
    )):
        raise ValueError("PERSONAL_GCP Gemini and BigQuery projects must equal PERSONAL_GCP_PROJECT_ID.")
    for variable, value in (
        ("GOOGLE_CLOUD_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT")),
        ("BIGQUERY_PROJECT_ID", os.getenv("BIGQUERY_PROJECT_ID")),
        ("GEMINI_PROJECT_ID", _legacy_gemini_project),
    ):
        if value and value.casefold() not in {
            PERSONAL_GCP_PROJECT_ID.casefold(), PERSONAL_GEMINI_PROJECT_ID.casefold(),
        }:
            raise ValueError(f"{variable} conflicts with the explicit PERSONAL_GCP project settings.")
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS is not allowed in PERSONAL_GCP; use independent credential files.")

    GEMINI_CREDENTIALS_FILE = _optional_credentials_file("PERSONAL_GCP_GEMINI_CREDENTIALS_FILE", required=True)
    BIGQUERY_CREDENTIALS_FILE = _optional_credentials_file("PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE", required=True)
    if GEMINI_CREDENTIALS_FILE == BIGQUERY_CREDENTIALS_FILE:
        raise ValueError("PERSONAL_GCP Gemini and BigQuery credential files must be distinct.")

    allowed_datasets = tuple(
        value.strip() for value in os.getenv("PERSONAL_GCP_ALLOWED_DATASETS", "").split(",") if value.strip()
    )
    if not allowed_datasets:
        raise ValueError("PERSONAL_GCP_ALLOWED_DATASETS must contain approved datasets.")
    if len(allowed_datasets) != len(set(allowed_datasets)) or any(
        not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", dataset) for dataset in allowed_datasets
    ):
        raise ValueError("PERSONAL_GCP_ALLOWED_DATASETS contains invalid or duplicate dataset IDs.")
    PERSONAL_GCP_ALLOWED_DATASETS = frozenset(dataset.casefold() for dataset in allowed_datasets)

    PERSONAL_GCP_DEFAULT_DATASET_ID = _required_profile_value("PERSONAL_GCP_DEFAULT_DATASET_ID")
    PERSONAL_GCP_DEFAULT_TABLE_ID = _required_profile_value("PERSONAL_GCP_DEFAULT_TABLE_ID")
    if PERSONAL_GCP_DEFAULT_DATASET_ID.casefold() not in PERSONAL_GCP_ALLOWED_DATASETS:
        raise ValueError("PERSONAL_GCP_DEFAULT_DATASET_ID must be in PERSONAL_GCP_ALLOWED_DATASETS.")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", PERSONAL_GCP_DEFAULT_TABLE_ID):
        raise ValueError("PERSONAL_GCP_DEFAULT_TABLE_ID is invalid.")

    PERSONAL_GCP_GEMINI_LOCATION = _required_profile_value("PERSONAL_GCP_GEMINI_LOCATION")
    PERSONAL_GCP_BIGQUERY_LOCATION = _required_profile_value("PERSONAL_GCP_BIGQUERY_LOCATION")
    PROJECT_ID = PERSONAL_GCP_PROJECT_ID
    GEMINI_PROJECT_ID = PERSONAL_GEMINI_PROJECT_ID
    GEMINI_EFFECTIVE_PROJECT_ID = PERSONAL_GEMINI_PROJECT_ID
    BIGQUERY_PROJECT_ID = PERSONAL_BIGQUERY_PROJECT_ID
    BIGQUERY_DATASET_ID = PERSONAL_GCP_DEFAULT_DATASET_ID
    BIGQUERY_TABLE_ID = PERSONAL_GCP_DEFAULT_TABLE_ID
    LOCATION = PERSONAL_GCP_GEMINI_LOCATION
    BIGQUERY_LOCATION = PERSONAL_GCP_BIGQUERY_LOCATION
elif RUNTIME_PROFILE == "LOCAL":
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "mos-gcp-host-dev-prj")
    LOCATION = _location
    BIGQUERY_PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID", PROJECT_ID)
    BIGQUERY_DATASET_ID = os.getenv("BIGQUERY_DATASET_ID", "dw_nsp_api_views")
    BIGQUERY_TABLE_ID = os.getenv("BIGQUERY_TABLE_ID", "nsp_base_view_01")
    GEMINI_PROJECT_ID = _legacy_gemini_project
    if not GEMINI_PROJECT_ID:
        raise ValueError("GEMINI_PROJECT_ID is required when RUNTIME_PROFILE=LOCAL.")
    client_projects = {
        CLIENT_GCP_PROJECT_ID.casefold(), PROJECT_ID.casefold(), BIGQUERY_PROJECT_ID.casefold(),
    }
    if GEMINI_PROJECT_ID.casefold() in client_projects:
        raise ValueError("GEMINI_PROJECT_ID must differ from the client GCP projects in LOCAL mode.")
    GEMINI_EFFECTIVE_PROJECT_ID = GEMINI_PROJECT_ID
    BIGQUERY_LOCATION = os.getenv("BIGQUERY_LOCATION", _location)
    LOCAL_SCHEMA_BACKUP_PATH = os.getenv("LOCAL_SCHEMA_BACKUP_PATH")
    LOCAL_SCHEMA_BACKUP_PATH = LOCAL_SCHEMA_BACKUP_PATH.strip() or None if LOCAL_SCHEMA_BACKUP_PATH is not None else None
    if not LOCAL_SCHEMA_BACKUP_PATH:
        raise ValueError("LOCAL_SCHEMA_BACKUP_PATH is required when RUNTIME_PROFILE=LOCAL.")
    GEMINI_CREDENTIALS_FILE = _optional_credentials_file("GEMINI_CREDENTIALS_FILE")
elif RUNTIME_PROFILE == "CLIENT_GCP":
    PROJECT_ID = CLIENT_GCP_PROJECT_ID
    LOCATION = _location
    BIGQUERY_PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID", PROJECT_ID)
    BIGQUERY_DATASET_ID = os.getenv("BIGQUERY_DATASET_ID", "dw_nsp_api_views")
    BIGQUERY_TABLE_ID = os.getenv("BIGQUERY_TABLE_ID", "nsp_base_view_01")
    GEMINI_PROJECT_ID = _legacy_gemini_project
    GEMINI_EFFECTIVE_PROJECT_ID = PROJECT_ID
    BIGQUERY_LOCATION = os.getenv("BIGQUERY_LOCATION", _location)
    GEMINI_CREDENTIALS_FILE = _optional_credentials_file("GEMINI_CREDENTIALS_FILE")
    BIGQUERY_CREDENTIALS_FILE = _optional_credentials_file("BIGQUERY_CREDENTIALS_FILE")

LOCAL_SCHEMA_BACKUP_PATH = locals().get("LOCAL_SCHEMA_BACKUP_PATH", None)
BIGQUERY_DYNAMIC_ALLOWED_DATASETS = frozenset(
    dataset.strip().casefold()
    for dataset in os.getenv(
        "PERSONAL_GCP_ALLOWED_DATASETS" if RUNTIME_PROFILE == "PERSONAL_GCP" else "BIGQUERY_DYNAMIC_ALLOWED_DATASETS",
        "",
    ).split(",")
    if dataset.strip()
)


def load_gemini_credentials():
    if not GEMINI_CREDENTIALS_FILE:
        return None
    import google.auth

    credentials, _ = google.auth.load_credentials_from_file(
        GEMINI_CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return credentials


def load_bigquery_credentials():
    if not BIGQUERY_CREDENTIALS_FILE:
        return None
    import google.auth

    credentials, _ = google.auth.load_credentials_from_file(
        BIGQUERY_CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return credentials


def get_bigquery_client_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"project": BIGQUERY_PROJECT_ID}
    credentials = load_bigquery_credentials()
    if credentials is not None:
        kwargs["credentials"] = credentials
    return kwargs

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
if RUNTIME_PROFILE == "PERSONAL_GCP":
    BIGQUERY_DYNAMIC_CATALOG_ENABLED = False
    BIGQUERY_DYNAMIC_ROUTING_ENABLED = False
else:
    BIGQUERY_DYNAMIC_CATALOG_ENABLED = os.getenv("BIGQUERY_DYNAMIC_CATALOG_ENABLED", "true").casefold() in {"1", "true", "yes"}
    BIGQUERY_DYNAMIC_ROUTING_ENABLED = os.getenv("BIGQUERY_DYNAMIC_ROUTING_ENABLED", "true").casefold() in {"1", "true", "yes"}
if RUNTIME_PROFILE == "PERSONAL_GCP":
    BIGQUERY_DYNAMIC_ALLOWED_DATASETS = PERSONAL_GCP_ALLOWED_DATASETS
else:
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