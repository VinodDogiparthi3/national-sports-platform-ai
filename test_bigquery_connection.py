from google.cloud import bigquery


PROJECT_ID = "mos-gcp-host-dev-prj"
DATASET_ID = "dw_nsp_api_views"
TABLE_ID = "nsp_base_view_01"

FULL_TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"


client = bigquery.Client(project=PROJECT_ID)

table = client.get_table(FULL_TABLE_ID)

print("\nBIGQUERY CONNECTION: SUCCESS")
print(f"Project : {PROJECT_ID}")
print(f"Dataset : {DATASET_ID}")
print(f"Table   : {TABLE_ID}")

print(f"\nNumber of columns: {len(table.schema)}")

print("\nSCHEMA:")
for field in table.schema:
    print(
        f"{field.name:40} "
        f"{field.field_type:15} "
        f"{field.mode}"
    )
print(f"Table type: {table.table_type}")