import json
import re

from google import genai

from config import PROJECT_ID, LOCATION, MODEL
from schema_metadata import get_bigquery_schema


client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)

OUT_OF_SCOPE_CLARIFICATIONS = {
    "en": (
        "Could you please ask a question related to the National Sports Platform (NSP)? "
        "I can help with athlete registrations, sports, federations, age groups, "
        "gender, nationalities, and other available NSP data."
    ),
    "ar": (
        "يرجى طرح سؤال يتعلق بالمنصة الوطنية للرياضة (NSP). "
        "يمكنني مساعدتك في التسجيلات الرياضية والرياضات والاتحادات والفئات العمرية "
        "والجنس والجنسيات وغيرها من بيانات المنصة المتاحة."
    ),
}
OUT_OF_SCOPE_CLARIFICATION = OUT_OF_SCOPE_CLARIFICATIONS["en"]


def get_out_of_scope_clarification(language: str) -> str:
    return OUT_OF_SCOPE_CLARIFICATIONS.get(language, OUT_OF_SCOPE_CLARIFICATION)


def translate_texts(texts: list[str], target_language: str) -> list[str]:
    if not texts:
        return []

    if target_language not in {"en", "ar"}:
        raise ValueError("Unsupported translation language.")

    language_name = "Arabic" if target_language == "ar" else "English"
    prompt = f"""
Translate each text into clear, natural {language_name} for display in a chat transcript.
Return only a JSON array of strings in the same order, with exactly one translation per input.
Do not answer the questions.
Preserve proper names, sports and federation names, nationalities, database values, codes, identifiers, numbers, and quoted values exactly as written.

Texts:
{json.dumps(texts, ensure_ascii=False)}
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )
    text = response.text.strip()
    fenced_json = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_json:
        text = fenced_json.group(1).strip()

    translations = json.loads(text)
    if (
        not isinstance(translations, list)
        or len(translations) != len(texts)
        or not all(isinstance(translation, str) for translation in translations)
    ):
        raise ValueError("Invalid translation response.")
    return translations


def translate_questions_to_arabic(questions: list[str]) -> list[str]:
    return translate_texts(questions, "ar")


def _clean_sql_output(text: str) -> str:
    text = text.strip()
    fenced_sql = re.fullmatch(
        r"```(?:sql|bigquery)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return fenced_sql.group(1).strip() if fenced_sql else text


def generate_sql(question: str, language: str = "en") -> str:
    schema = get_bigquery_schema()
    response_language = "Arabic" if language == "ar" else "English"
    clarification = get_out_of_scope_clarification(language)
    prompt = f"""
You are a Google BigQuery SQL expert.

Generate a Google BigQuery Standard SQL SELECT query
for the user's question.

Schema:
{schema}

Rules:
- First determine whether the question is about National Sports Platform data supported by this schema.
- If the question is unrelated, asks for database/schema metadata, or is not supported by the schema, return exactly: CLARIFICATION: {clarification}
- Write any clarification in {response_language}.
- Do not generate SQL for questions that need clarification.
- Only for supported NSP questions, generate BigQuery Standard SQL.
- Only SELECT queries are allowed.
- Use the fully qualified table name provided in the schema.
- Use only tables and columns provided in the schema.
- Do not invent tables or columns.
- This dataset contains National Sports Platform data about registrations, athletes, sports, federations, regions, and participation.
- If the question is unrelated to this data or cannot be answered using the listed schema, return exactly: CLARIFICATION: {clarification}
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


def _build_answer_prompt(question: str, columns, rows, language: str = "en") -> str:
    result_text = f"Columns: {columns}\n"
    response_language = "Arabic" if language == "ar" else "English"

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
- Write all explanations, headings, and connective text in {response_language} only; do not include parallel translations.
- Preserve database values such as names, sports, federations, nationalities, and group labels exactly as returned. Do not translate, transliterate, or modify those values.
- Do not invent information.
- Use only the database results.
- Do not mention SQL, schemas, table or column names, row counts, or database internals.
- Make the response clean, concise, and user-friendly.
- Start directly with a short descriptive heading.
- Use bullet points for grouped or categorical results.
- Format numbers with commas when appropriate.
- Replace null/None category values with "Not specified".
- If a total can be calculated directly from the returned rows, show it as:
    {"**إجمالي التسجيلات:**" if language == "ar" else "**Total Registrations:**"} <value>
- Do not repeat information already shown in the bullet points.
- Do not add an introductory sentence.
- Do not add a concluding explanatory sentence.
- Use Markdown formatting.
"""


def generate_answer(
    question: str,
    columns,
    rows,
    language: str = "en",
) -> str:
    prompt = _build_answer_prompt(question, columns, rows, language)
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )

    return response.text.strip()


def generate_answer_stream(question: str, columns, rows, language: str = "en"):
    prompt = _build_answer_prompt(question, columns, rows, language)

    for chunk in client.models.generate_content_stream(
        model=MODEL,
        contents=prompt,
    ):
        if chunk.text:
            yield chunk.text