import os

import psycopg
from google import genai

from sql_guardrails import validate_sql


# -------------------------
# Configuration
# -------------------------

PROJECT_ID = "project-c3cf90ac-efd1-4b3e-871"
LOCATION = "global"
MODEL = "gemini-3.8-flash"

DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "national_sports_poc"
DB_USER = "postgres"
DB_PASSWORD = os.environ["POSTGRES_PASSWORD"]


# -------------------------
# Gemini
# -------------------------

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)

question = "How many registrations are there for each sport?"

schema = """
Table: individual_affiliation

Columns:
registration_date TIMESTAMP
registration_year INTEGER
registration_quarter VARCHAR
individual_type VARCHAR
gender VARCHAR
age INTEGER
status VARCHAR
nationality VARCHAR
federation VARCHAR
sports_type_en VARCHAR
AgeGroup_en VARCHAR
isparalympicsport_en VARCHAR
city VARCHAR
region VARCHAR
"""

prompt = f"""
You are a PostgreSQL expert.

Generate a PostgreSQL SELECT query for the user's question.

Schema:
{schema}

Rules:
- Only SELECT queries.
- Use only the provided table and columns.
- Do not invent columns.
- Return SQL only.
- No markdown.
- For grouped/count results, order by the count descending.

Question:
{question}
"""

response = client.models.generate_content(
    model=MODEL,
    contents=prompt,
)

generated_sql = response.text.strip()

print("\nQUESTION:")
print(question)

print("\nGEMINI GENERATED SQL:")
print(generated_sql)


# -------------------------
# Guardrail
# -------------------------

safe_sql = validate_sql(generated_sql)

print("\nGUARDRAIL:")
print("SQL validation passed.")


# -------------------------
# PostgreSQL
# -------------------------

connection_string = (
    f"host={DB_HOST} "
    f"port={DB_PORT} "
    f"dbname={DB_NAME} "
    f"user={DB_USER} "
    f"password={DB_PASSWORD}"
)

with psycopg.connect(connection_string) as conn:
    # Extra protection: transaction is read-only.
    conn.execute("SET TRANSACTION READ ONLY")

    with conn.cursor() as cur:
        cur.execute(safe_sql)

        columns = [
            description.name
            for description in cur.description
        ]

        rows = cur.fetchall()


# -------------------------
# Results
# -------------------------

print("\nDATABASE RESULTS:")
print(columns)

# -------------------------
# Gemini Final Answer
# -------------------------

result_text = "\n".join(
    [", ".join(map(str, row)) for row in rows]
)

answer_prompt = f"""
You are an AI assistant for the National Sports Platform.

Answer the user's question using ONLY the database results provided below.

User question:
{question}

Database results:
{result_text}

Rules:
- Do not invent information.
- Use only the provided database results.
- Give a clear and concise answer.
- Mention important totals or highest values when useful.
"""

answer_response = client.models.generate_content(
    model=MODEL,
    contents=answer_prompt,
)

final_answer = answer_response.text.strip()

print("\nFINAL AI ANSWER:")
print(final_answer)

for row in rows:
    print(row)