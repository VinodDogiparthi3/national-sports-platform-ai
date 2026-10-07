import re

from google import genai

from config import PROJECT_ID, LOCATION, MODEL
from schema_metadata import get_bigquery_schema


client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)

OUT_OF_SCOPE_CLARIFICATION = (
    "Could you please ask a question related to the National Sports Platform (NSP)? "
    "I can help with athlete registrations, sports, federations, age groups, "
    "gender, nationalities, and other available NSP data."
)


def _clean_sql_output(text: str) -> str:
    text = text.strip()
    fenced_sql = re.fullmatch(
        r"```(?:sql|bigquery)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return fenced_sql.group(1).strip() if fenced_sql else text


def generate_sql(question: str) -> str:
    schema = get_bigquery_schema()
    prompt = f"""
You are a Google BigQuery SQL expert.

Generate a Google BigQuery Standard SQL SELECT query
for the user's question.

Schema:
{schema}

Rules:
- First determine whether the question is about National Sports Platform data supported by this schema.
- If the question is unrelated, asks for database/schema metadata, or is not supported by the schema, return exactly: CLARIFICATION: {OUT_OF_SCOPE_CLARIFICATION}
- Do not generate SQL for questions that need clarification.
- Only for supported NSP questions, generate BigQuery Standard SQL.
- Only SELECT queries are allowed.
- Use the fully qualified table name provided in the schema.
- Use only tables and columns provided in the schema.
- Do not invent tables or columns.
- This dataset contains National Sports Platform data about registrations, athletes, sports, federations, regions, and participation.
- If the question is unrelated to this data or cannot be answered using the listed schema, return exactly: CLARIFICATION: {OUT_OF_SCOPE_CLARIFICATION}
- Do not infer meanings for ambiguous code fields.
- Interpret obvious spelling or typing errors only when the intended question is clear.
- Do not alter names, sports, identifiers, codes, or filter values unless the correction is unambiguous.
- If a typo makes the request ambiguous, return exactly: CLARIFICATION: <one short question asking the user to clarify>.
- Otherwise, return SQL only.
- Do not return markdown.
- For grouped/count results, order by the count descending.

Question:
{question}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )

    return _clean_sql_output(response.text)


def _build_answer_prompt(question: str, columns, rows) -> str:
    result_text = f"Columns: {columns}\n"

    for row in rows:
        result_text += f"{row}\n"

    return f"""
You are a friendly AI assistant for the National Sports Platform.

Answer the user's question using ONLY the database
results provided below.

User question:
{question}

Database results:
{result_text}

Rules:
- Do not invent information.
- Use only the database results.
- Do not mention SQL, schemas, table or column names, row counts, or database internals.
- Make the response clean, concise, and user-friendly.
- Start directly with a short descriptive heading.
- Use bullet points for grouped or categorical results.
- Format numbers with commas when appropriate.
- Replace null/None category values with "Not specified".
- If a total can be calculated directly from the returned rows, show it as:
  **Total Registrations:** <value>
- Do not repeat information already shown in the bullet points.
- Do not add an introductory sentence.
- Do not add a concluding explanatory sentence.
- Use Markdown formatting.
"""


def generate_answer(
    question: str,
    columns,
    rows,
) -> str:
    prompt = _build_answer_prompt(question, columns, rows)
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )

    return response.text.strip()


def generate_answer_stream(question: str, columns, rows):
    prompt = _build_answer_prompt(question, columns, rows)

    for chunk in client.models.generate_content_stream(
        model=MODEL,
        contents=prompt,
    ):
        if chunk.text:
            yield chunk.text