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
_lock = threading.Lock()


def clear_schema_cache() -> None:
    with _lock:
        _cache.clear()


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

    columns = [
        f"{name} {ftype} {mode}"
        for name, ftype, mode in get_view_columns(view.view)
    ]
    view_instructions = ""
    if view.view == "population_estimates_statistics_view_01":
        view_instructions = """

Population estimate row semantics:
- The row whose `age_group` is 'Total' already contains the overall population estimate.
- For an overall estimate, filter to `age_group` = 'Total'; do not sum it with age bands.
- For age-group breakdowns, exclude `age_group` = 'Total' before calculating or presenting band totals.
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