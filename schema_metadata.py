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
from view_catalog import APPROVED_VIEWS, get_view

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
    """Return (name, type, mode) per column from BigQuery metadata (no row reads), cached with a TTL."""
    view = get_view(view_id)
    if view is None:
        raise ValueError(f"View is not approved: {view_id}")

    key = view.view.lower()
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < SCHEMA_CACHE_TTL_SECONDS:
            return cached[1]

    client = bigquery.Client(project=BIGQUERY_PROJECT_ID)
    table = client.get_table(view.full_id)
    columns = [(f.name, f.field_type, f.mode) for f in table.schema]
    with _lock:
        _cache[key] = (time.monotonic(), columns)
    return columns


def get_filter_values(view_id: str) -> dict[str, list[str]]:
    """Distinct stored values of the view's filter columns; {} when none are configured or the lookup fails."""
    view = get_view(view_id)
    columns = FILTER_VALUE_COLUMNS.get(view.view.lower(), {}) if view else {}
    if not columns:
        return {}

    key = view.view.lower()
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
                f"SELECT DISTINCT `{column}` FROM `{view.full_id}` WHERE `{column}` IS NOT NULL ORDER BY `{column}`"
            )
            _, rows = execute_sql(sql)
            values[column] = [str(row[0]) for row in rows if row[0] is not None]
    except Exception as e:
        print(f"\nERROR LOADING FILTER VALUES FOR {view.full_id}: {type(e).__name__}: {e}")
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


def get_bigquery_schema(view_id: str | None = None) -> str:
    """
    Build the schema context for Gemini from BigQuery metadata.
    Defaults to the NSP view when view_id is omitted.
    """
    view = get_view(view_id) if view_id else get_view(BIGQUERY_TABLE_ID)
    if view is None:
        raise ValueError(f"View is not approved: {view_id}")

    view_columns = get_view_columns(view.view)
    columns = [
        f"{name} {ftype} {mode}"
        for name, ftype, mode in view_columns
    ]
    view_instructions = ""
    if view.view == "nsp_base_view_01" and any(
        name.casefold() == "idv_classificationrecordid" for name, _, _ in view_columns
    ):
        view_instructions = """

Athlete count semantics:
- For questions asking how many athletes are registered or for an athlete count, count distinct `idv_classificationrecordid`.
- Do not use `contactid` for this athlete registration metric.
"""
    if view.view == "population_estimates_statistics_view_01":
        view_instructions = """

Population estimate row semantics:
- The row whose `age_group` is 'Total' already contains the overall population estimate.
- For an overall estimate, filter to `age_group` = 'Total'; do not sum it with age bands.
- For age-group breakdowns, exclude `age_group` = 'Total' before calculating or presenting band totals.
"""
    for column, stored in get_filter_values(view.view).items():
        if stored:
            view_instructions += f"""
Known stored values of `{column}` (exact spelling, in the stored language):
{chr(10).join(f"- {value}" for value in stored)}
- Filter `{column}` only with these exact values (or a substring of one with LIKE), mapping the user's wording in English or Arabic to the matching value.
- If no value matches, do not guess: return a CLARIFICATION.
"""

    schema_text = f"""
BigQuery table:
`{view.full_id}`

Business name: {view.display_name}
Business domain: {view.domain}
Description: {view.description}

Columns:
{chr(10).join(columns)}

Important:
- Generate Google BigQuery Standard SQL.
- Use the fully qualified table name:
  `{view.full_id}`
- Use only columns listed above (names may be Arabic or English; backtick-quote them exactly as listed).
- Do not invent columns.
- Do not infer the meaning of ambiguous code fields.
{view_instructions}
"""

    return schema_text


if __name__ == "__main__":
    print(get_bigquery_schema())