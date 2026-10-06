from gemini_service import generate_sql
from sql_guardrails import validate_sql


question = "How many registrations are there for each sport?"

generated_sql = generate_sql(question)

print("\nGENERATED SQL:")
print(generated_sql)

safe_sql = validate_sql(generated_sql)

print("\nVALIDATED SQL:")
print(safe_sql)