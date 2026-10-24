import os
import json
from pathlib import Path
import re

import sqlglot
from sqlglot import exp as sql_exp


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

_semantic_search_enabled = os.getenv("SEMANTIC_SEARCH_ENABLED", "false")
if _semantic_search_enabled not in {"true", "false"}:
    raise ValueError("SEMANTIC_SEARCH_ENABLED must be exactly true or false.")
SEMANTIC_SEARCH_ENABLED = _semantic_search_enabled == "true"

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


def _parse_dataset_object_allowlist(name: str) -> dict[str, frozenset[str]]:
    try:
        raw = json.loads(os.getenv(name, "{}"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} must be valid JSON.") from error
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must map dataset IDs to arrays of object IDs.")
    result = {}
    for dataset, objects in raw.items():
        if not isinstance(dataset, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", dataset):
            raise ValueError(f"{name} contains an invalid dataset ID.")
        if not isinstance(objects, list) or not objects or not all(
            isinstance(obj, str) and re.fullmatch(r"[A-Za-z0-9_]{1,1024}", obj) for obj in objects
        ):
            raise ValueError(f"{name} must contain nonempty arrays of valid object IDs.")
        normalized = [obj.casefold() for obj in objects]
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{name} contains duplicate object IDs.")
        key = dataset.casefold()
        if key in result:
            raise ValueError(f"{name} contains duplicate dataset IDs.")
        result[key] = frozenset(normalized)
    return result


def _parse_personal_column_allowlist() -> dict[str, tuple[tuple[str, str, str], ...]]:
    raw = os.getenv("PERSONAL_GCP_ALLOWED_COLUMNS_JSON", "").strip()
    if not raw:
        raise ValueError("PERSONAL_GCP_ALLOWED_COLUMNS_JSON is required.")
    try:
        raw_columns = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("PERSONAL_GCP_ALLOWED_COLUMNS_JSON must be valid JSON.") from error
    if not isinstance(raw_columns, dict) or not raw_columns:
        raise ValueError("PERSONAL_GCP_ALLOWED_COLUMNS_JSON must map dataset.object IDs to column metadata arrays.")
    result = {}
    for source_id, columns in raw_columns.items():
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,1024}\.[A-Za-z0-9_]{1,1024}", source_id):
            raise ValueError("PERSONAL_GCP_ALLOWED_COLUMNS_JSON contains an invalid source ID.")
        key = source_id.casefold()
        if key in result or not isinstance(columns, list) or not columns:
            raise ValueError("PERSONAL_GCP_ALLOWED_COLUMNS_JSON contains duplicate sources or empty column lists.")
        names = set()
        normalized = []
        for column in columns:
            if not isinstance(column, dict) or set(column) != {"name", "data_type", "nullability"}:
                raise ValueError("Each personal column must specify only name, data_type, and nullability.")
            name, data_type, nullability = column["name"], column["data_type"], column["nullability"]
            if (
                not isinstance(name, str)
                or not name
                or name != name.strip()
                or len(name) > 300
                or not name.isprintable()
                or not isinstance(data_type, str)
                or not re.fullmatch(r"[A-Za-z0-9_<>(), ]{1,64}", data_type)
                or nullability not in {"NULLABLE", "REQUIRED", "REPEATED"}
                or name.casefold() in names
            ):
                raise ValueError("PERSONAL_GCP_ALLOWED_COLUMNS_JSON contains invalid or duplicate column metadata.")
            names.add(name.casefold())
            normalized.append((name, data_type, nullability))
        result[key] = tuple(normalized)
    return result


PERSONAL_GCP_EXECUTION_ENABLED = False
PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT: str | None = None
PERSONAL_GCP_SYNTHETIC_DATASET_ID = "nsp_ai_synthetic"
PERSONAL_GCP_SYNTHETIC_TABLE_ID = "synthetic_athletes"
PERSONAL_GCP_ALLOWED_DATASETS: frozenset[str] = frozenset()
PERSONAL_GCP_DATASET_MAPPINGS: dict[str, str] = {}
PERSONAL_GCP_ALLOWED_TABLES: dict[str, frozenset[str]] = {}
PERSONAL_GCP_ALLOWED_VIEWS: dict[str, frozenset[str]] = {}
PERSONAL_GCP_ALLOWED_COLUMNS: dict[str, tuple[tuple[str, str, str], ...]] = {}
PERSONAL_GCP_VIEW_DEFINITIONS: dict[str, str] = {}
PERSONAL_GCP_VIEW_DEPENDENCY_CLOSURE: dict[str, frozenset[str]] = {}
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
    _personal_execution_requested = os.getenv("PERSONAL_GCP_EXECUTION_ENABLED", "false").strip().casefold() in {"1", "true", "yes"}
    PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT = (
        os.getenv("PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT", "").strip() or None
    )
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
    if _personal_execution_requested:
        if PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT != PERSONAL_GCP_PROJECT_ID:
            raise ValueError("PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT must exactly match PERSONAL_GCP_PROJECT_ID.")
        PERSONAL_GCP_EXECUTION_ENABLED = True
    elif (
        PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT
        and PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT != PERSONAL_GCP_PROJECT_ID
    ):
        raise ValueError("PERSONAL_GCP_EXECUTION_CONFIRM_PROJECT must match PERSONAL_GCP_PROJECT_ID.")
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

    dataset_mapping_json = os.getenv("PERSONAL_GCP_DATASET_MAPPINGS", "").strip()
    if not dataset_mapping_json:
        raise ValueError("PERSONAL_GCP_DATASET_MAPPINGS is required.")
    try:
        mapping_pairs = json.loads(dataset_mapping_json, object_pairs_hook=list)
    except json.JSONDecodeError as error:
        raise ValueError("PERSONAL_GCP_DATASET_MAPPINGS must be valid JSON.") from error
    if not isinstance(mapping_pairs, list) or not mapping_pairs or not all(
        isinstance(pair, tuple) and len(pair) == 2 for pair in mapping_pairs
    ):
        raise ValueError("PERSONAL_GCP_DATASET_MAPPINGS must explicitly map client dataset IDs to personal dataset IDs.")
    for client_dataset, personal_dataset in mapping_pairs:
        if (
            not isinstance(client_dataset, str)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", client_dataset)
            or not isinstance(personal_dataset, str)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", personal_dataset)
        ):
            raise ValueError("PERSONAL_GCP_DATASET_MAPPINGS contains an invalid dataset ID.")
        key = client_dataset.casefold()
        if key in PERSONAL_GCP_DATASET_MAPPINGS:
            raise ValueError("PERSONAL_GCP_DATASET_MAPPINGS contains duplicate client dataset IDs.")
        PERSONAL_GCP_DATASET_MAPPINGS[key] = personal_dataset
    mapped_personal_datasets = {dataset.casefold() for dataset in PERSONAL_GCP_DATASET_MAPPINGS.values()}
    if PERSONAL_GCP_ALLOWED_DATASETS != mapped_personal_datasets:
        raise ValueError("PERSONAL_GCP_DATASET_MAPPINGS targets must exactly match PERSONAL_GCP_ALLOWED_DATASETS.")

    PERSONAL_GCP_ALLOWED_TABLES = _parse_dataset_object_allowlist("PERSONAL_GCP_ALLOWED_TABLES")
    if not PERSONAL_GCP_ALLOWED_TABLES:
        raise ValueError("PERSONAL_GCP_ALLOWED_TABLES must explicitly allow personal tables.")
    PERSONAL_GCP_ALLOWED_VIEWS = _parse_dataset_object_allowlist("PERSONAL_GCP_ALLOWED_VIEWS")
    for allowlist_name, allowlist in (
        ("PERSONAL_GCP_ALLOWED_TABLES", PERSONAL_GCP_ALLOWED_TABLES),
        ("PERSONAL_GCP_ALLOWED_VIEWS", PERSONAL_GCP_ALLOWED_VIEWS),
    ):
        if not set(allowlist) <= PERSONAL_GCP_ALLOWED_DATASETS:
            raise ValueError(f"{allowlist_name} references a dataset outside PERSONAL_GCP_ALLOWED_DATASETS.")

    PERSONAL_GCP_ALLOWED_COLUMNS = _parse_personal_column_allowlist()
    approved_source_ids = {
        f"{dataset}.{source}".casefold()
        for allowlist in (PERSONAL_GCP_ALLOWED_TABLES, PERSONAL_GCP_ALLOWED_VIEWS)
        for dataset, sources in allowlist.items()
        for source in sources
    }
    if set(PERSONAL_GCP_ALLOWED_COLUMNS) != approved_source_ids:
        raise ValueError("PERSONAL_GCP_ALLOWED_COLUMNS_JSON must exactly cover approved personal tables and views.")

    raw_view_definitions = os.getenv("PERSONAL_GCP_VIEW_DEFINITIONS_JSON", "{}")
    try:
        view_definitions = json.loads(raw_view_definitions, object_pairs_hook=list)
    except json.JSONDecodeError as error:
        raise ValueError("PERSONAL_GCP_VIEW_DEFINITIONS_JSON must be valid JSON.") from error
    if not isinstance(view_definitions, list) or not all(
        isinstance(pair, tuple) and len(pair) == 2 for pair in view_definitions
    ):
        raise ValueError("PERSONAL_GCP_VIEW_DEFINITIONS_JSON must map personal dataset.view IDs to SQL strings.")
    allowed_view_ids = {
        f"{dataset}.{view}".casefold()
        for dataset, views in PERSONAL_GCP_ALLOWED_VIEWS.items()
        for view in views
    }
    for source_id, definition in view_definitions:
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,1024}\.[A-Za-z0-9_]{1,1024}", source_id):
            raise ValueError("PERSONAL_GCP_VIEW_DEFINITIONS_JSON contains an invalid source ID.")
        key = source_id.casefold()
        if key not in allowed_view_ids:
            raise ValueError("PERSONAL_GCP_VIEW_DEFINITIONS_JSON includes a view outside PERSONAL_GCP_ALLOWED_VIEWS.")
        if key in PERSONAL_GCP_VIEW_DEFINITIONS or not isinstance(definition, str) or not definition.strip():
            raise ValueError("PERSONAL_GCP_VIEW_DEFINITIONS_JSON contains a duplicate or empty view definition.")
        try:
            statements = sqlglot.parse(definition, read="bigquery")
        except Exception as error:
            raise ValueError("PERSONAL_GCP_VIEW_DEFINITIONS_JSON contains invalid BigQuery SQL.") from error
        if len(statements) != 1 or not isinstance(statements[0], sql_exp.Select):
            raise ValueError("Personal view definitions must contain exactly one SELECT statement.")
        PERSONAL_GCP_VIEW_DEFINITIONS[key] = definition
    if set(PERSONAL_GCP_VIEW_DEFINITIONS) != allowed_view_ids:
        raise ValueError("Every PERSONAL_GCP_ALLOWED_VIEWS entry must have an explicit PERSONAL_GCP_VIEW_DEFINITIONS_JSON definition.")

    from sql_sources import resolved_table_references
    view_dependencies = {}
    view_direct_tables = {}
    for view_id, definition in PERSONAL_GCP_VIEW_DEFINITIONS.items():
        try:
            references = resolved_table_references(definition)
        except ValueError as error:
            raise ValueError(f"Personal view definition {view_id} is invalid.") from error
        if not references:
            raise ValueError("Personal view definitions must reference at least one approved personal source.")
        dependencies = []
        direct_tables = []
        for table in references:
            project_id = (table.catalog or "").casefold()
            dataset_id = (table.db or "").casefold()
            object_id = (table.name or "").casefold()
            if project_id != PERSONAL_GCP_PROJECT_ID.casefold() or dataset_id not in PERSONAL_GCP_ALLOWED_DATASETS:
                raise ValueError("Personal view definitions must reference only approved personal-project datasets.")
            source_id = f"{dataset_id}.{object_id}"
            if object_id in PERSONAL_GCP_ALLOWED_TABLES.get(dataset_id, frozenset()):
                direct_tables.append(source_id)
                continue
            if object_id in PERSONAL_GCP_ALLOWED_VIEWS.get(dataset_id, frozenset()):
                if source_id not in PERSONAL_GCP_VIEW_DEFINITIONS:
                    raise ValueError("Nested personal views require an explicit approved view definition.")
                dependencies.append(source_id)
                continue
            raise ValueError("Personal view definitions reference a table or view outside the explicit allowlists.")
        view_dependencies[view_id] = tuple(dependencies)
        view_direct_tables[view_id] = tuple(direct_tables)

    visiting_views = set()
    checked_views = set()

    def validate_view_dependencies(view_id: str) -> None:
        if view_id in visiting_views:
            raise ValueError("Cyclic PERSONAL_GCP view definitions are not allowed.")
        if view_id in checked_views:
            return
        visiting_views.add(view_id)
        for dependency in view_dependencies[view_id]:
            validate_view_dependencies(dependency)
        visiting_views.remove(view_id)
        checked_views.add(view_id)

    for view_id in view_dependencies:
        validate_view_dependencies(view_id)

    def view_dependency_closure(view_id: str) -> frozenset[str]:
        closure = set(view_direct_tables[view_id])
        for dependency in view_dependencies[view_id]:
            closure.add(dependency)
            closure |= view_dependency_closure(dependency)
        return frozenset(closure)

    PERSONAL_GCP_VIEW_DEPENDENCY_CLOSURE = {
        view_id: view_dependency_closure(view_id) for view_id in view_dependencies
    }

    PERSONAL_GCP_DEFAULT_DATASET_ID = _required_profile_value("PERSONAL_GCP_DEFAULT_DATASET_ID")
    PERSONAL_GCP_DEFAULT_TABLE_ID = _required_profile_value("PERSONAL_GCP_DEFAULT_TABLE_ID")
    if PERSONAL_GCP_DEFAULT_DATASET_ID.casefold() not in PERSONAL_GCP_ALLOWED_DATASETS:
        raise ValueError("PERSONAL_GCP_DEFAULT_DATASET_ID must be in PERSONAL_GCP_ALLOWED_DATASETS.")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", PERSONAL_GCP_DEFAULT_TABLE_ID):
        raise ValueError("PERSONAL_GCP_DEFAULT_TABLE_ID is invalid.")
    default_dataset = PERSONAL_GCP_DEFAULT_DATASET_ID.casefold()
    default_object = PERSONAL_GCP_DEFAULT_TABLE_ID.casefold()
    if (
        default_object not in PERSONAL_GCP_ALLOWED_TABLES.get(default_dataset, frozenset())
        and default_object not in PERSONAL_GCP_ALLOWED_VIEWS.get(default_dataset, frozenset())
    ):
        raise ValueError("PERSONAL_GCP_DEFAULT_TABLE_ID must be explicitly approved as a table or view.")

    if PERSONAL_GCP_EXECUTION_ENABLED:
        synthetic_dataset = PERSONAL_GCP_SYNTHETIC_DATASET_ID
        synthetic_table = PERSONAL_GCP_SYNTHETIC_TABLE_ID
        if PERSONAL_GCP_ALLOWED_DATASETS != frozenset({synthetic_dataset}):
            raise ValueError("PERSONAL_GCP execution is restricted to nsp_ai_synthetic.")
        if PERSONAL_GCP_ALLOWED_TABLES != {synthetic_dataset: frozenset({synthetic_table})}:
            raise ValueError("PERSONAL_GCP execution is restricted to nsp_ai_synthetic.synthetic_athletes.")
        if PERSONAL_GCP_ALLOWED_VIEWS:
            raise ValueError("PERSONAL_GCP execution does not allow views in this checkpoint.")
        if (
            PERSONAL_GCP_DEFAULT_DATASET_ID.casefold() != synthetic_dataset
            or PERSONAL_GCP_DEFAULT_TABLE_ID.casefold() != synthetic_table
        ):
            raise ValueError("PERSONAL_GCP defaults must target nsp_ai_synthetic.synthetic_athletes.")
        expected_synthetic_columns = {
            "synthetic_id": ("string", "nullable"),
            "sport": ("string", "nullable"),
            "age_years": ("int64", "nullable"),
            "category": ("string", "nullable"),
            "country_label": ("string", "nullable"),
            "association_label": ("string", "nullable"),
            "registration_year": ("int64", "nullable"),
        }
        configured_synthetic_columns = {}
        for name, data_type, nullability in PERSONAL_GCP_ALLOWED_COLUMNS[
            f"{synthetic_dataset}.{synthetic_table}"
        ]:
            normalized_type = data_type.casefold()
            if normalized_type == "integer":
                normalized_type = "int64"
            configured_synthetic_columns[name.casefold()] = (
                normalized_type,
                nullability.casefold(),
            )
        if configured_synthetic_columns != expected_synthetic_columns:
            raise ValueError("PERSONAL_GCP execution requires the approved synthetic athlete schema.")

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
    if RUNTIME_PROFILE == "PERSONAL_GCP" and PERSONAL_GCP_EXECUTION_ENABLED:
        _validate_personal_impersonated_credentials(
            credentials,
            "nsp-ai-gemini@" + PERSONAL_GCP_PROJECT_ID + ".iam.gserviceaccount.com",
            "Gemini",
        )
    return credentials


def _validate_personal_impersonated_credentials(credentials, expected_target: str, name: str) -> None:
    from google.auth import impersonated_credentials

    if not isinstance(credentials, impersonated_credentials.Credentials):
        raise ValueError(f"PERSONAL_GCP {name} credentials must use service-account impersonation.")
    if credentials.service_account_email != expected_target:
        raise ValueError(f"PERSONAL_GCP {name} credentials target the wrong service account.")
    if credentials.quota_project_id != PERSONAL_GCP_PROJECT_ID:
        raise ValueError(f"PERSONAL_GCP {name} credentials must use the personal quota project.")


def map_client_dataset_to_personal(dataset_id: str) -> str:
    if RUNTIME_PROFILE != "PERSONAL_GCP":
        raise ValueError("Client-to-personal dataset mapping is available only in PERSONAL_GCP.")
    try:
        return PERSONAL_GCP_DATASET_MAPPINGS[dataset_id.casefold()]
    except (AttributeError, KeyError) as error:
        raise ValueError("No approved personal dataset mapping exists for this client dataset.") from error


def map_personal_dataset_to_client(dataset_id: str) -> tuple[str, ...]:
    if RUNTIME_PROFILE != "PERSONAL_GCP":
        raise ValueError("Personal-to-client dataset mapping is available only in PERSONAL_GCP.")
    matches = [
        client_dataset
        for client_dataset, personal_dataset in PERSONAL_GCP_DATASET_MAPPINGS.items()
        if personal_dataset.casefold() == dataset_id.casefold()
    ]
    if not matches:
        raise ValueError("No approved client dataset mapping exists for this personal dataset.")
    return tuple(matches)


def load_bigquery_credentials():
    if not BIGQUERY_CREDENTIALS_FILE:
        return None
    import google.auth

    credentials, _ = google.auth.load_credentials_from_file(
        BIGQUERY_CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    if RUNTIME_PROFILE == "PERSONAL_GCP" and PERSONAL_GCP_EXECUTION_ENABLED:
        _validate_personal_impersonated_credentials(
            credentials,
            "nsp-ai-bigquery@" + PERSONAL_GCP_PROJECT_ID + ".iam.gserviceaccount.com",
            "BigQuery",
        )
    return credentials


def get_bigquery_client_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"project": BIGQUERY_PROJECT_ID}
    if BIGQUERY_JOB_LOCATION:
        kwargs["location"] = BIGQUERY_JOB_LOCATION
    credentials = load_bigquery_credentials()
    if RUNTIME_PROFILE == "PERSONAL_GCP" and credentials is None:
        raise ValueError("PERSONAL_GCP BigQuery credentials are required; shared ADC fallback is disabled.")
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
# Execution safeguards
# -------------------------

def _positive_int_setting(name: str, default: int | None, maximum: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer.") from error
    if value <= 0 or (maximum is not None and value > maximum):
        limit = f" no greater than {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be a positive integer{limit}.")
    return value


_PERSONAL = RUNTIME_PROFILE == "PERSONAL_GCP"
_BIGQUERY_LOCATION_PATTERN = r"[A-Za-z]{2,}(?:-[A-Za-z0-9]+)*"

if _PERSONAL:
    BIGQUERY_JOB_LOCATION = PERSONAL_GCP_BIGQUERY_LOCATION
else:
    # CLIENT_GCP/LOCAL send a job location only when one is configured explicitly.
    BIGQUERY_JOB_LOCATION = (os.getenv("BIGQUERY_LOCATION") or "").strip() or None
if BIGQUERY_JOB_LOCATION is not None and (
    BIGQUERY_JOB_LOCATION.casefold() == "global"
    or not re.fullmatch(_BIGQUERY_LOCATION_PATTERN, BIGQUERY_JOB_LOCATION)
):
    raise ValueError("The BigQuery location must be a BigQuery region or multi-region, not global.")

PERSONAL_GCP_MAX_JOB_TIMEOUT_SECONDS = 60
# CLIENT_GCP/LOCAL add server-side job cancellation only when explicitly configured.
BIGQUERY_JOB_TIMEOUT_SECONDS = _positive_int_setting(
    "BIGQUERY_JOB_TIMEOUT_SECONDS",
    min(QUERY_TIMEOUT_SECONDS, PERSONAL_GCP_MAX_JOB_TIMEOUT_SECONDS) if _PERSONAL else None,
    PERSONAL_GCP_MAX_JOB_TIMEOUT_SECONDS if _PERSONAL else None,
)
BIGQUERY_JOB_LABELS = {
    "app": "nsp-ai",
    "runtime-profile": RUNTIME_PROFILE.casefold().replace("_", "-"),
}

# Unset Gemini limits keep CLIENT_GCP/LOCAL behavior unchanged; PERSONAL_GCP is always bounded.
GEMINI_MAX_OUTPUT_TOKENS = _positive_int_setting(
    "GEMINI_MAX_OUTPUT_TOKENS", 4096 if _PERSONAL else None, 8192 if _PERSONAL else None,
)
GEMINI_REQUEST_TIMEOUT_SECONDS = _positive_int_setting(
    "GEMINI_REQUEST_TIMEOUT_SECONDS", 60 if _PERSONAL else None, 300 if _PERSONAL else None,
)
GEMINI_MAX_CALLS_PER_REQUEST = _positive_int_setting(
    "GEMINI_MAX_CALLS_PER_REQUEST", 8 if _PERSONAL else None, 20 if _PERSONAL else None,
)

# Per-process counters only: they reset on restart and are not shared across workers or instances.
PERSONAL_GCP_MAX_GEMINI_CALLS_PER_PROCESS: int | None = None
PERSONAL_GCP_MAX_BIGQUERY_JOBS_PER_PROCESS: int | None = None
PERSONAL_GCP_MAX_BIGQUERY_BYTES_PER_PROCESS: int | None = None
PERSONAL_GCP_ALLOW_REMOTE_CLIENTS = False
PERSONAL_GCP_API_TOKEN: str | None = None
PERSONAL_GCP_RATE_LIMIT_PER_MINUTE: int | None = None
if _PERSONAL:
    PERSONAL_GCP_MAX_GEMINI_CALLS_PER_PROCESS = _positive_int_setting(
        "PERSONAL_GCP_MAX_GEMINI_CALLS_PER_PROCESS", 200, 1000,
    )
    PERSONAL_GCP_MAX_BIGQUERY_JOBS_PER_PROCESS = _positive_int_setting(
        "PERSONAL_GCP_MAX_BIGQUERY_JOBS_PER_PROCESS", 100, 1000,
    )
    PERSONAL_GCP_MAX_BIGQUERY_BYTES_PER_PROCESS = _positive_int_setting(
        "PERSONAL_GCP_MAX_BIGQUERY_BYTES_PER_PROCESS", 4 * HARD_MAX_BYTES_PROCESSED, 40 * HARD_MAX_BYTES_PROCESSED,
    )
    PERSONAL_GCP_ALLOW_REMOTE_CLIENTS = (
        os.getenv("PERSONAL_GCP_ALLOW_REMOTE_CLIENTS", "false").strip().casefold() in {"1", "true", "yes"}
    )
    PERSONAL_GCP_API_TOKEN = (os.getenv("PERSONAL_GCP_API_TOKEN") or "").strip() or None
    if PERSONAL_GCP_API_TOKEN is not None and len(PERSONAL_GCP_API_TOKEN) < 32:
        raise ValueError("PERSONAL_GCP_API_TOKEN must contain at least 32 characters.")
    if PERSONAL_GCP_ALLOW_REMOTE_CLIENTS and PERSONAL_GCP_API_TOKEN is None:
        raise ValueError("PERSONAL_GCP_API_TOKEN is required when PERSONAL_GCP_ALLOW_REMOTE_CLIENTS is enabled.")
    PERSONAL_GCP_RATE_LIMIT_PER_MINUTE = _positive_int_setting("PERSONAL_GCP_RATE_LIMIT_PER_MINUTE", 10, 120)


# -------------------------
# Excel export
# -------------------------

EXPORT_MAX_ROWS = int(os.getenv("EXPORT_MAX_ROWS", "500000"))
EXPORT_CONTEXT_TTL_SECONDS = int(os.getenv("EXPORT_CONTEXT_TTL_SECONDS", str(24 * 3600)))
EXPORT_DOWNLOAD_TTL_SECONDS = int(os.getenv("EXPORT_DOWNLOAD_TTL_SECONDS", "3600"))
# Must be identical on every Cloud Run instance, otherwise tokens only work on the issuing instance.
EXPORT_SECRET_KEY = os.getenv("EXPORT_SECRET_KEY")