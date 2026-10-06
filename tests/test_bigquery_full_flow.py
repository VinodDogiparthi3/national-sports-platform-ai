from gemini_service import generate_sql, generate_answer
from sql_guardrails import validate_sql
from bigquery_database import execute_sql


question = "How many registrations are there for each sport?"

# 1. Natural language → SQL
generated_sql = generate_sql(question)

# 2. Security validation
safe_sql = validate_sql(generated_sql)

# 3. BigQuery execution
columns, rows = execute_sql(safe_sql)

# 4. Database results → natural-language answer
answer = generate_answer(
    question,
    columns,
    rows
)

print("\nQUESTION:")
print(question)

print("\nVALIDATED SQL:")
print(safe_sql)

print("\nFINAL AI ANSWER:")
print(answer)