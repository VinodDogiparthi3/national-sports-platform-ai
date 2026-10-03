from google.cloud import bigquery


PROJECT_ID = "mos-gcp-host-dev-prj"
DATASET_ID = "dw_nsp_api_views"
TABLE_ID = "nsp_base_view_01"

FULL_TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"


def get_bigquery_schema() -> str:
    """
    Read table metadata from BigQuery and build
    schema context for Gemini.

    This retrieves metadata only, not table rows.
    """

    client = bigquery.Client(project=PROJECT_ID)

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