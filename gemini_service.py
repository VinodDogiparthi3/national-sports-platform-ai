from google import genai

from config import PROJECT_ID, LOCATION, MODEL
from schema_metadata import get_bigquery_schema


client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)


def generate_sql(question: str) -> str:
    schema = get_bigquery_schema()
    prompt = f"""
You are a Google BigQuery SQL expert.

Generate a Google BigQuery Standard SQL SELECT query
for the user's question.

Schema:
{schema}

Rules:
- Generate BigQuery Standard SQL only.
- Only SELECT queries are allowed.
- Use the fully qualified table name provided in the schema.
- Use only tables and columns provided in the schema.
- Do not invent tables or columns.
- Do not infer meanings for ambiguous code fields.
- Return SQL only.
- Do not return markdown.
- For grouped/count results, order by the count descending.

Question:
{question}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )

    return response.text.strip()


def generate_answer(
    question: str,
    columns,
    rows,
) -> str:

    result_text = f"Columns: {columns}\n"

    for row in rows:
        result_text += f"{row}\n"

    prompt = f"""
You are an AI assistant for the National Sports Platform.

Answer the user's question using ONLY the database
results provided below.

User question:
{question}

Database results:
{result_text}

Rules:
- Do not invent information.
- Use only the database results.
- Keep the answer clear and concise.
- Mention totals or highest values when useful.
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )

    return response.text.strip()