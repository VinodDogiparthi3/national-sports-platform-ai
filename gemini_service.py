import json
import re

from google import genai

from config import PROJECT_ID, LOCATION, MODEL
from nafes_schema import get_nafes_schema_prompt
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


def _parse_json_response(text: str):
    text = text.strip()
    fenced_json = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return json.loads(fenced_json.group(1).strip() if fenced_json else text)


def extract_export_analysis_question(question: str, previous_question: str | None) -> str | None:
    """Return the data request inside an Excel export message, or None when it only refers to earlier results."""
    prompt = f"""
The user wants an Excel export in a National Sports Platform data chat.
Decide whether the message describes a NEW data request or only asks to export previously shown results.

Return only JSON: {{"analysis_question": <string or null>}}
- Return null when the message only asks to export/download/generate a file for "this", "these", "the above", "the previous", "the complete data", or gives no data description at all.
- Otherwise return the data request alone, in the user's language, with every Excel/export/download/format wording removed.
- Do not answer the question and do not add details the user did not state.

Previous question in this conversation: {json.dumps(previous_question, ensure_ascii=False)}
User message: {json.dumps(question, ensure_ascii=False)}
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )
    result = _parse_json_response(response.text)
    analysis_question = result.get("analysis_question") if isinstance(result, dict) else None
    if isinstance(analysis_question, str) and analysis_question.strip():
        return analysis_question.strip()
    return None


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
    translations = _parse_json_response(response.text)
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


def generate_nafes_sql(question: str, language: str = "en") -> str:
        response_language = "Arabic" if language == "ar" else "English"
        prompt = f"""
You are generating read-only BigQuery Standard SQL for the Nafes facility
licensing and request follow-up POC.

Approved data contract:
{get_nafes_schema_prompt()}

SQL rules:
- Return one SELECT statement only, with fully qualified approved views.
- Never use SELECT * or any column not in the approved schema.
- For license detail, create license_by_id grouped by License_ID. Project
    License_ID as _license_id inside the CTE. Return it only as license_id when
    the user asks to list licenses; never return it for aggregate questions.
    Calculate facility_id_variants, expiry_date_variants,
    license_status_variants, and license_type_variants with null-aware distinct
    counts. Only use ANY_VALUE after checking its variant count. Then create
    unambiguous_licenses selecting _license_id, facility_id, license_expiry_date,
    license_source_status, license_type_name, and all four variant fields,
    filtered with AND so every variant count equals 1.
- For requests, create request_by_id grouped by Request_ID. Project Request_ID
    only as _request_id inside the CTE; never return an identifier. Calculate
    facility_id_variants, request_status_variants, and request_date_variants with
    null-aware distinct counts. Then create unambiguous_requests selecting
    _request_id, facility_id, request_date, request_status and the three variant
    fields, filtered with AND so every variant count equals 1.
- Aggregate licenses and requests separately by facility before joining.
- For two-view facility results, create licenses_by_facility from
    unambiguous_licenses and requests_by_facility from unambiguous_requests;
    in each, filter facility_id IS NOT NULL and TRIM(facility_id) != '', group
    by facility_id, and count unique_license_count/associated_request_count.
    LEFT JOIN those aggregate CTEs on facility_id; never join raw view rows.
- Counts must count unique licenses/requests, not source rows.
- Never imply a direct request-to-license relationship. Use facility-level
    association only.
- Keep License_Status_Name as the recorded source status. Calculate expiry
    status separately; do not call a license "active" or a request "pending" based
    on assumptions.
- Any user-supplied facility ID must use @facility_id. Expiry-window logic must
    use @window_days and CURRENT_DATE('UTC').
- Use @freshness_threshold_days rather than hardcoding the request freshness
    threshold. Derive latest_request_date from MAX(Request_Date).
- Never join or group facility-level results using NULL or blank facility IDs.
    For joins, require both facility IDs to be non-NULL and non-blank.
- Keep results to approved display fields and aliases: facility_id, license_id,
  unique_license_count, license_expiry_date, license_source_status,
  calculated_expiry_status, conflicting_license_count,
  associated_request_count, request_status, latest_request_date,
  days_since_latest_request, data_freshness_warning, region_name, city_name,
  license_type_name, facility_type_name, days_to_expiry.
- For a license-list question, return a bounded detail list with facility_id,
  _license_id AS license_id, license_type_name, license_expiry_date, and
    calculated_expiry_status, sorted by expiry date. Because the query is filtered
    to the requested next-date window, set calculated_expiry_status to the fixed
    label 'Expiring Soon' for each returned row; do not label it merely valid or
    not expired. Do not return license IDs for count-only questions.
- Group request results by the exact recorded Request_Status_Name. Do not
    classify statuses as pending, approved, rejected, or under review.
- Do not hardcode the latest request date. Use MAX(Request_Date) and
    CURRENT_DATE('UTC') to calculate freshness dynamically.
- Add a reasonable LIMIT. Return SQL only; no markdown or explanation.

Question in {response_language}:
{question}
"""
        response = client.models.generate_content(model=MODEL, contents=prompt)
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


def generate_nafes_answer(
    question: str,
    columns,
    rows,
    language: str = "en",
) -> str:
    response_language = "Arabic" if language == "ar" else "English"
    prompt = f"""
You are a business-friendly assistant for the National Sports Platform Nafes
facility licensing and request follow-up POC.

Answer the user's question using only the approved query results below.

Question:
{question}

Approved results:
Columns: {columns}
{chr(10).join(str(row) for row in rows)}

Rules:
- Write in {response_language}.
- Preserve facility IDs and recorded source status values exactly.
- Keep source license status separate from calculated expiry status.
- Do not infer that any request is pending, approved, rejected, or under review.
- Describe requests only as associated facility requests; do not claim a request
  created, renewed, or modified a license.
- Do not invent facility names, dates, counts, status meanings, causes, or actions.
- Do not reveal license/request IDs, contact details, account/CR data, addresses,
  phone numbers, emails, or coordinates.
- Be concise, use a heading and bullets where useful, and format counts clearly.
"""
    response = client.models.generate_content(model=MODEL, contents=prompt)
    return response.text.strip()


def generate_answer_stream(question: str, columns, rows, language: str = "en"):
    prompt = _build_answer_prompt(question, columns, rows, language)

    for chunk in client.models.generate_content_stream(
        model=MODEL,
        contents=prompt,
    ):
        if chunk.text:
            yield chunk.text