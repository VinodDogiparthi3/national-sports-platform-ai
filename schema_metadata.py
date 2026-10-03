from google.cloud import bigquery

from config import (
    BIGQUERY_DATASET_ID,
    BIGQUERY_PROJECT_ID,
    BIGQUERY_TABLE_ID,
)

FULL_TABLE_ID = (
    f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"
)


def get_bigquery_schema() -> str:
    """
    Read table metadata from BigQuery and build
    schema context for Gemini.

    This retrieves metadata only, not table rows.
    """

    client = bigquery.Client(project=BIGQUERY_PROJECT_ID)

    table = client.get_table(FULL_TABLE_ID)

    columns = []

    for field in table.schema:
        columns.append(
            f"{field.name} {field.field_type} {field.mode}"
        )

    schema_text = f"""
BigQuery table:
`{FULL_TABLE_ID}`

Columns:
{chr(10).join(columns)}

Important:
- Generate Google BigQuery Standard SQL.
- Use the fully qualified table name:
  `{FULL_TABLE_ID}`
- Use only columns listed above.
- Do not invent columns.
- Do not infer the meaning of ambiguous code fields.
"""

    return schema_text


if __name__ == "__main__":
    print(get_bigquery_schema())