from gemini_service import generate_sql
from sql_guardrails import validate_sql
from bigquery_database import dry_run_sql


question = "How many registrations are there for each sport?"

generated_sql = generate_sql(question)
safe_sql = validate_sql(generated_sql)

print("\nVALIDATED SQL:")
print(safe_sql)

bytes_processed = dry_run_sql(safe_sql)

print("\nBIGQUERY DRY RUN: SUCCESS")
print(f"Estimated bytes processed: {bytes_processed:,}")
print(f"Estimated MB: {bytes_processed / (1024 * 1024):.2f}")