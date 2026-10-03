from google.cloud import bigquery

from config import BIGQUERY_PROJECT_ID, BIGQUERY_DATASET_ID, BIGQUERY_TABLE_ID

FULL_TABLE_ID = (
    f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"
)


client = bigquery.Client(project=BIGQUERY_PROJECT_ID)

table = client.get_table(FULL_TABLE_ID)

print("\nBIGQUERY CONNECTION: SUCCESS")
print(f"Project : {BIGQUERY_PROJECT_ID}")
print(f"Dataset : {BIGQUERY_DATASET_ID}")
print(f"Table   : {BIGQUERY_TABLE_ID}")

print(f"\nNumber of columns: {len(table.schema)}")

print("\nSCHEMA:")
for field in table.schema:
    print(
        f"{field.name:40} "
        f"{field.field_type:15} "
        f"{field.mode}"
    )
print(f"Table type: {table.table_type}")