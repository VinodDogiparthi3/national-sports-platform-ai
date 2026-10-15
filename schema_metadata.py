import threading
import time
from concurrent.futures import ThreadPoolExecutor

from google.cloud import bigquery

from config import (
    BIGQUERY_DATASET_ID,
    BIGQUERY_PROJECT_ID,
    BIGQUERY_TABLE_ID,
    SCHEMA_CACHE_TTL_SECONDS,
)
from view_catalog import APPROVED_VIEWS, get_approved_tables, get_source, get_view

FULL_TABLE_ID = (
    f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"
)

_cache: dict[str, tuple[float, list[tuple[str, str, str]]]] = {}
_values_cache: dict[str, tuple[float, dict[str, list[str]]]] = {}
_lock = threading.Lock()

# view -> column -> (English label, Arabic label); values are read from BigQuery, never hardcoded.
FILTER_VALUE_COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    "nsp_base_view_01": {"region": ("region", "المنطقة")},
}


def clear_schema_cache() -> None:
    with _lock:
        _cache.clear()
        _values_cache.clear()


def get_view_columns(view_id: str) -> list[tuple[str, str, str]]:
    """Return columns for an approved view or table, cached with a TTL."""
    source = get_source(view_id)
    if source is None:
        raise ValueError(f"Source is not approved: {view_id}")

    key = source.full_id.lower()
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < SCHEMA_CACHE_TTL_SECONDS:
            return cached[1]

    client = bigquery.Client(project=BIGQUERY_PROJECT_ID)
    table = client.get_table(source.full_id)
    columns = [(field.name, field.field_type, field.mode) for field in table.schema]
    with _lock:
        _cache[key] = (time.monotonic(), columns)
    return columns


def get_filter_values(view_id: str) -> dict[str, list[str]]:
    """Distinct stored values of configured source filter columns; {} when unavailable."""
    source = get_source(view_id)
    columns = FILTER_VALUE_COLUMNS.get(source.source_id.lower(), {}) if source else {}
    if not columns:
        return {}

    key = source.source_id.lower()
    with _lock:
        cached = _values_cache.get(key)
        if cached and time.monotonic() - cached[0] < SCHEMA_CACHE_TTL_SECONDS:
            return cached[1]

    from bigquery_database import execute_sql
    from sql_guardrails import validate_sql

    values: dict[str, list[str]] = {}
    try:
        for column in columns:
            sql = validate_sql(
                f"SELECT DISTINCT `{column}` FROM `{source.full_id}` WHERE `{column}` IS NOT NULL ORDER BY `{column}`"
            )
            _, rows = execute_sql(sql)
            values[column] = [str(row[0]) for row in rows if row[0] is not None]
    except Exception as e:
        print(f"\nERROR LOADING FILTER VALUES FOR {source.full_id}: {type(e).__name__}: {e}")
        return {}

    with _lock:
        _values_cache[key] = (time.monotonic(), values)
    return values


def get_all_view_columns() -> dict[str, list[tuple[str, str, str]]]:
    """Columns for every approved view; views whose metadata cannot be read are omitted."""

    def load(view):
        try:
            return view.view, get_view_columns(view.view)
        except Exception as e:
            print(f"\nERROR LOADING SCHEMA FOR {view.full_id}: {type(e).__name__}: {e}")
            return view.view, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        return {name: cols for name, cols in pool.map(load, APPROVED_VIEWS) if cols}


def get_all_source_columns() -> dict[str, list[tuple[str, str, str]]]:
    """Columns for approved views and physical tables; unavailable metadata is omitted."""
    sources = [*APPROVED_VIEWS, *get_approved_tables()]

    def load(source):
        try:
            return source.source_id, get_view_columns(source.source_id)
        except Exception as error:
            print(f"\nERROR LOADING SCHEMA FOR {source.full_id}: {type(error).__name__}: {error}")
            return source.source_id, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        return {name: columns for name, columns in pool.map(load, sources) if columns}


def get_bigquery_schema(view_id: str | None = None) -> str:
    """
    Build the schema context for Gemini from BigQuery metadata.
    Defaults to the NSP view when view_id is omitted.
    """
    source = get_source(view_id) if view_id else get_view(BIGQUERY_TABLE_ID)
    if source is None:
        raise ValueError(f"Source is not approved: {view_id}")

    view_columns = get_view_columns(source.source_id)
    columns = [
        f"{name} {ftype} {mode}"
        for name, ftype, mode in view_columns
    ]
    view_instructions = ""
    if source.source_id == "nsp_base_view_01" and any(
        name.casefold() == "idv_classificationrecordid" for name, _, _ in view_columns
    ):
        view_instructions = """

Athlete count semantics:
- For questions asking how many athletes are registered or for an athlete count, count distinct `idv_classificationrecordid`.
- Do not use `contactid` for this athlete registration metric.
"""
    if source.source_id == "population_estimates_statistics_view_01":
        view_instructions = """

Population estimate row semantics:
- The row whose `age_group` is 'Total' already contains the overall population estimate.
- For an overall estimate, filter to `age_group` = 'Total'; do not sum it with age bands.
- For age-group breakdowns, exclude `age_group` = 'Total' before calculating or presenting band totals.
"""
    for column, stored in get_filter_values(source.source_id).items():
        if stored:
            view_instructions += f"""
Known stored values of `{column}` (exact spelling, in the stored language):
{chr(10).join(f"- {value}" for value in stored)}
- Filter `{column}` only with these exact values (or a substring of one with LIKE), mapping the user's wording in English or Arabic to the matching value.
- If no value matches, do not guess: return a CLARIFICATION.
"""

    schema_text = f"""
BigQuery table:
`{source.full_id}`

Object type: {source.object_type}
Business name: {source.display_name}
Business domain: {source.domain}
Description: {source.description}

Columns:
{chr(10).join(columns)}

Important:
- Generate Google BigQuery Standard SQL.
- Use the fully qualified table name:
    `{source.full_id}`
- Use only columns listed above (names may be Arabic or English; backtick-quote them exactly as listed).
- Do not invent columns.
- Do not infer the meaning of ambiguous code fields.
{view_instructions}
"""

    return schema_text


if __name__ == "__main__":
    print(get_bigquery_schema())