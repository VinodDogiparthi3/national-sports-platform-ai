from google.cloud import bigquery

from bigquery_access import require_bigquery_access
from config import MAX_BYTES_PROCESSED, QUERY_TIMEOUT_SECONDS, RUNTIME_PROFILE, get_bigquery_client_kwargs

client = bigquery.Client(**get_bigquery_client_kwargs()) if RUNTIME_PROFILE == "CLIENT_GCP" else None


class QueryBudgetExceeded(ValueError):
    pass


def dry_run_sql(sql: str) -> int:
    require_bigquery_access("dry run")

    job_config = bigquery.QueryJobConfig(
        dry_run=True,
        use_query_cache=False
    )

    query_job = client.query(
        sql,
        job_config=job_config
    )

    bytes_processed = (
        query_job.total_bytes_processed or 0
    )

    if bytes_processed > MAX_BYTES_PROCESSED:
        raise QueryBudgetExceeded(
            "Query exceeds the configured "
            f"{MAX_BYTES_PROCESSED:,}-byte processing limit. "
            f"Estimated bytes: {bytes_processed}"
        )

    return bytes_processed


def _preflight_sqls(sqls: list[str]) -> list[int]:
    require_bigquery_access("query preflight")
    estimates = [dry_run_sql(sql) for sql in sqls]
    total_estimated = sum(estimates)
    if total_estimated > MAX_BYTES_PROCESSED:
        raise QueryBudgetExceeded(
            "Combined queries exceed the configured "
            f"{MAX_BYTES_PROCESSED:,}-byte request budget. "
            f"Estimated bytes: {total_estimated}"
        )
    return estimates


def _execute_sql_after_dry_run(sql: str):
    require_bigquery_access("query execution")
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_PROCESSED,
        use_query_cache=True,
    )
    query_job = client.query(sql, job_config=job_config)
    result = query_job.result(max_results=100, timeout=QUERY_TIMEOUT_SECONDS)
    columns = [field.name for field in result.schema]
    rows = [tuple(row.values()) for row in result]
    return columns, rows


def execute_sql_batch(sqls: list[str]):
    """Dry-run every SQL statement against one combined 250 MiB request budget first."""
    require_bigquery_access("batch query execution")
    _preflight_sqls(sqls)
    return [_execute_sql_after_dry_run(sql) for sql in sqls]


def execute_sql(sql: str):
    require_bigquery_access("query execution")

    # Safety check before execution
    dry_run_sql(sql)

    return _execute_sql_after_dry_run(sql)


def _run_export_query(sql: str, page_size: int):
    require_bigquery_access("export query execution")
    # Same byte guard as normal answers; the query cache makes the later download re-run cheap.
    dry_run_sql(sql)

    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_PROCESSED,
        use_query_cache=True
    )

    return client.query(
        sql,
        job_config=job_config
    ).result(page_size=page_size, timeout=QUERY_TIMEOUT_SECONDS)


def get_export_row_count(sql: str) -> int:
    require_bigquery_access("export row counting")
    return int(_run_export_query(sql, page_size=1).total_rows or 0)


def get_export_row_counts(sqls: list[str]) -> list[int]:
    """Budget export preparation plus the later workbook download as one user request."""
    require_bigquery_access("export row counting")
    estimates = _preflight_sqls(sqls)
    complete_export_estimate = sum(estimates) * 2
    if complete_export_estimate > MAX_BYTES_PROCESSED:
        raise ValueError(
            "Combined export preparation and download exceed the configured "
            f"{MAX_BYTES_PROCESSED:,}-byte request budget. "
            f"Estimated bytes: {complete_export_estimate}"
        )
    row_counts = []
    for sql in sqls:
        result = client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=MAX_BYTES_PROCESSED,
                use_query_cache=True,
            ),
        ).result(page_size=1, timeout=QUERY_TIMEOUT_SECONDS)
        row_counts.append(int(result.total_rows or 0))
    return row_counts


def stream_query_rows(sql: str, page_size: int = 10000):
    """Return (columns, row iterator) without loading the full result into memory."""
    require_bigquery_access("query streaming")
    result = _run_export_query(sql, page_size=page_size)

    columns = [
        field.name
        for field in result.schema
    ]

    return columns, (tuple(row.values()) for row in result)


def stream_query_rows_batch(sqls: list[str], page_size: int = 10000):
    """Preflight all export queries together, then return one lazy row stream per query."""
    require_bigquery_access("batch query streaming")
    _preflight_sqls(sqls)
    sheets = []
    for sql in sqls:
        result = client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=MAX_BYTES_PROCESSED,
                use_query_cache=True,
            ),
        ).result(page_size=page_size, timeout=QUERY_TIMEOUT_SECONDS)
        columns = [field.name for field in result.schema]
        sheets.append((columns, (tuple(row.values()) for row in result)))
    return sheets