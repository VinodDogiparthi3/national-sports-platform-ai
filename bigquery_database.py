from google.cloud import bigquery

from config import BIGQUERY_PROJECT_ID, MAX_BYTES_PROCESSED, QUERY_TIMEOUT_SECONDS

client = bigquery.Client(project=BIGQUERY_PROJECT_ID)


def dry_run_sql(sql: str) -> int:

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
        raise ValueError(
            "Query exceeds the configured "
            f"{MAX_BYTES_PROCESSED:,}-byte processing limit. "
            f"Estimated bytes: {bytes_processed}"
        )

    return bytes_processed


def _preflight_sqls(sqls: list[str]) -> list[int]:
    estimates = [dry_run_sql(sql) for sql in sqls]
    total_estimated = sum(estimates)
    if total_estimated > MAX_BYTES_PROCESSED:
        raise ValueError(
            "Combined queries exceed the configured "
            f"{MAX_BYTES_PROCESSED:,}-byte request budget. "
            f"Estimated bytes: {total_estimated}"
        )
    return estimates


def _execute_sql_after_dry_run(sql: str):
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
    _preflight_sqls(sqls)
    return [_execute_sql_after_dry_run(sql) for sql in sqls]


def execute_sql(sql: str):

    # Safety check before execution
    dry_run_sql(sql)

    return _execute_sql_after_dry_run(sql)


def _run_export_query(sql: str, page_size: int):
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
    return int(_run_export_query(sql, page_size=1).total_rows or 0)


def get_export_row_counts(sqls: list[str]) -> list[int]:
    """Preflight every export query together before running any row-count query."""
    _preflight_sqls(sqls)
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
    result = _run_export_query(sql, page_size=page_size)

    columns = [
        field.name
        for field in result.schema
    ]

    return columns, (tuple(row.values()) for row in result)


def stream_query_rows_batch(sqls: list[str], page_size: int = 10000):
    """Preflight all export queries together, then return one lazy row stream per query."""
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