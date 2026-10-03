from google.cloud import bigquery


PROJECT_ID = "mos-gcp-host-dev-prj"

# 100 MB safety threshold for the POC
MAX_BYTES_PROCESSED = 250 * 1024 * 1024


client = bigquery.Client(project=PROJECT_ID)


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
            f"100 MB processing limit. "
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
        max_results=100
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