from gemini_service import generate_sql
from sql_guardrails import validate_sql
from bigquery_database import execute_sql


question = "How many registrations are there for each sport?"

# 1. Gemini generates BigQuery SQL
generated_sql = generate_sql(question)

print("\nGENERATED SQL:")
print(generated_sql)

# 2. Validate SQL
safe_sql = validate_sql(generated_sql)

print("\nVALIDATED SQL:")
print(safe_sql)

# 3. Execute against BigQuery
columns, rows = execute_sql(safe_sql)

print("\nBIGQUERY EXECUTION: SUCCESS")

print("\nCOLUMNS:")
print(columns)

print("\nRESULTS:")
for row in rows:
    print(row)