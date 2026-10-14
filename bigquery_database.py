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
def execute_sql(sql: str):

    # Safety check before execution
    dry_run_sql(sql)

    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_PROCESSED,
        use_query_cache=True
    )

    query_job = client.query(
        sql,
        job_config=job_config
    )

    result = query_job.result(
        max_results=100,
        timeout=QUERY_TIMEOUT_SECONDS,
    )

    columns = [
        field.name
        for field in result.schema
    ]

    rows = [
        tuple(row.values())
        for row in result
    ]

    return columns, rows


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


def stream_query_rows(sql: str, page_size: int = 10000):
    """Return (columns, row iterator) without loading the full result into memory."""
    result = _run_export_query(sql, page_size=page_size)

    columns = [
        field.name
        for field in result.schema
    ]

    return columns, (tuple(row.values()) for row in result)