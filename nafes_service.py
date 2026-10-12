import re
from datetime import date, datetime, timezone
from typing import Any

from google.cloud import bigquery

from bigquery_database import execute_sql_with_parameters
from nafes_schema import (
    ALLOWED_RESULT_ALIASES,
    APPROVED_COLUMNS_BY_VIEW,
    LICENSE_VIEW,
    NAFES_EXPIRY_WINDOW_DAYS,
    NAFES_MAX_BYTES_BILLED,
    NAFES_MAX_ROWS,
    NAFES_STALE_REQUEST_DAYS,
    REQUEST_VIEW,
    SENSITIVE_COLUMNS,
    LICENSE_INTERNAL_COLUMNS,
    REQUEST_INTERNAL_COLUMNS,
)
from sql_guardrails import validate_nafes_sql

_FACILITY_CONTEXT = re.compile(
    r"(?:\bfacility\s*(?:id|identifier)\b|معرف\s+المنشأة|رقم\s+المنشأة)"
    r"\s*(?::|=|#|\bis\b)?\s*(?:<([^>]+)>|([A-Za-z0-9][A-Za-z0-9-]{2,127}))",
    re.IGNORECASE,
)
_WINDOW_DAYS = re.compile(r"\b(\d{1,4})\s*(?:days?|أيام|يوم(?:ًا|اً)?)\b", re.IGNORECASE)
_PARAMETER = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)")


class NafesQuestionError(ValueError):
    pass


def is_nafes_question(question: str) -> bool:
    text = question.casefold()
    if re.search(r"\bnafes\b|\blicen[cs](?:e|es|ing)?\b|\bexpiry\b|\bexpire[sd]?\b", text):
        return True
    if re.search(r"\bfacility\s*(?:id|identifier|license|request|requests)\b", text):
        return True
    return bool(re.search(r"رخص|ترخيص|تراخيص|طلبات\s+المنش|المنشآت", question))


def is_pending_request_question(question: str) -> bool:
    request_terms = re.search(r"\brequests?\b|\bapplications?\b|الطلبات|طلبات", question, re.IGNORECASE)
    pending_terms = re.search(
        r"\bpending\b|\bopen\s+requests?\b|\boutstanding\b|قيد\s+الانتظار|معلّق|معلق|معلقة",
        question,
        re.IGNORECASE,
    )
    return bool(request_terms and pending_terms)


def _facility_specific_question(question: str) -> bool:
    return bool(_FACILITY_CONTEXT.search(question))


def extract_facility_id(question: str) -> str | None:
    match = _FACILITY_CONTEXT.search(question)
    if not match:
        return None
    value = (match.group(1) or match.group(2) or "").strip()
    if value.casefold() in {"id", "facility_id", "<id>"}:
        return None
    return value or None


def extract_window_days(question: str) -> int:
    match = _WINDOW_DAYS.search(question)
    if not match:
        return NAFES_EXPIRY_WINDOW_DAYS
    days = int(match.group(1))
    if not 1 <= days <= 3650:
        raise NafesQuestionError("The expiry window must be between 1 and 3650 days.")
    return days


def redact_facility_id(question: str) -> str:
    facility_id = extract_facility_id(question)
    return question.replace(facility_id, "[FACILITY_ID]", 1) if facility_id else question


def _query_parameters(sql: str, question: str) -> tuple[list[bigquery.ScalarQueryParameter], set[str]]:
    names = {name.lower() for name in _PARAMETER.findall(sql)}
    facility_id = extract_facility_id(question)
    if _facility_specific_question(question) and not facility_id:
        raise NafesQuestionError("Please include a valid facility ID in your question.")
    if "facility_id" in names and not facility_id:
        raise NafesQuestionError("Please include a valid facility ID in your question.")

    parameters = []
    if "facility_id" in names:
        parameters.append(bigquery.ScalarQueryParameter("facility_id", "STRING", facility_id))
    if "window_days" in names:
        parameters.append(
            bigquery.ScalarQueryParameter("window_days", "INT64", extract_window_days(question))
        )
    if "freshness_threshold_days" in names:
        parameters.append(
            bigquery.ScalarQueryParameter(
                "freshness_threshold_days", "INT64", NAFES_STALE_REQUEST_DAYS
            )
        )
    return parameters, names


def _validate_result(columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    safe_columns = set().union(*APPROVED_COLUMNS_BY_VIEW.values()) | ALLOWED_RESULT_ALIASES
    forbidden = (
        SENSITIVE_COLUMNS
        | (LICENSE_INTERNAL_COLUMNS - {"license_id"})
        | REQUEST_INTERNAL_COLUMNS
    )
    for column in columns:
        normalized = column.casefold()
        if normalized in forbidden or normalized not in safe_columns:
            raise ValueError(f"BigQuery returned a non-approved Nafes result column: {column}")
    if len(rows) > NAFES_MAX_ROWS:
        raise ValueError("The Nafes query exceeded its configured result limit.")


def execute_nafes_sql(
    sql: str,
    question: str,
) -> tuple[str, list[str], list[tuple[Any, ...]]]:
    required_parameters = {"facility_id"} if _facility_specific_question(question) else set()
    safe_sql = validate_nafes_sql(
        sql,
        row_limit=NAFES_MAX_ROWS,
        required_parameters=required_parameters,
    )
    parameters, _ = _query_parameters(safe_sql, question)
    columns, rows = execute_sql_with_parameters(
        safe_sql,
        parameters,
        maximum_bytes_billed=NAFES_MAX_BYTES_BILLED,
        max_results=NAFES_MAX_ROWS,
    )
    _validate_result(columns, rows)
    return safe_sql, columns, rows


_DATA_QUALITY_SQL = f"""
WITH license_by_id AS (
  SELECT
    License_ID AS _license_id,
    COUNT(*) AS license_row_count,
    COUNT(DISTINCT IFNULL(TRIM(Facility_ID), '')) AS facility_id_variants,
    COUNT(DISTINCT IFNULL(CAST(ntw_expiryDate AS STRING), '')) AS expiry_date_variants,
    COUNT(DISTINCT IFNULL(License_Status_Name, '')) AS license_status_variants,
    COUNT(DISTINCT IFNULL(License_Type_Name, '')) AS license_type_variants,
    COUNTIF(ntw_expiryDate IS NULL) AS missing_expiry_rows,
    COUNTIF(Facility_ID IS NULL OR TRIM(Facility_ID) = '') AS missing_facility_rows
  FROM `{LICENSE_VIEW}`
  WHERE License_ID IS NOT NULL AND TRIM(License_ID) != ''
  GROUP BY License_ID
),
request_by_id AS (
  SELECT
    Request_ID AS _request_id,
    COUNT(*) AS request_row_count,
    COUNT(DISTINCT IFNULL(TRIM(FacilityId), '')) AS facility_id_variants,
    COUNT(DISTINCT IFNULL(Request_Status_Name, '')) AS request_status_variants,
    COUNT(DISTINCT IFNULL(CAST(Request_Date AS STRING), '')) AS request_date_variants,
    COUNTIF(FacilityId IS NULL OR TRIM(FacilityId) = '') AS missing_facility_rows,
    MAX(Request_Date) AS latest_request_date
  FROM `{REQUEST_VIEW}`
  WHERE Request_ID IS NOT NULL AND TRIM(Request_ID) != ''
  GROUP BY Request_ID
),
license_quality AS (
  SELECT
    COUNTIF(license_row_count > 1) AS duplicate_license_id_count,
    COUNTIF(facility_id_variants > 1 OR expiry_date_variants > 1
            OR license_status_variants > 1 OR license_type_variants > 1) AS conflicting_license_id_count,
        COUNTIF(missing_expiry_rows = license_row_count) AS licenses_missing_expiry_date,
        COUNTIF(missing_facility_rows > 0) AS licenses_missing_facility_id
  FROM license_by_id
),
request_quality AS (
  SELECT
    COUNTIF(request_row_count > 1) AS duplicate_request_id_count,
    COUNTIF(facility_id_variants > 1 OR request_status_variants > 1
            OR request_date_variants > 1) AS conflicting_request_id_count,
    COUNTIF(missing_facility_rows > 0) AS requests_missing_facility_id,
    MAX(latest_request_date) AS latest_request_date
  FROM request_by_id
)
SELECT
  l.duplicate_license_id_count,
  l.conflicting_license_id_count,
    l.licenses_missing_expiry_date,
    l.licenses_missing_facility_id,
  r.duplicate_request_id_count,
  r.conflicting_request_id_count,
  r.requests_missing_facility_id,
  r.latest_request_date
FROM license_quality AS l
CROSS JOIN request_quality AS r
""".strip()


def get_nafes_data_quality() -> dict[str, Any]:
    safe_sql = validate_nafes_sql(
        _DATA_QUALITY_SQL,
        row_limit=1,
        enforce_deduplication=False,
    )
    columns, rows = execute_sql_with_parameters(
        safe_sql,
        [],
        maximum_bytes_billed=NAFES_MAX_BYTES_BILLED,
        max_results=1,
    )
    _validate_result(columns, rows)
    if not rows:
        return {
            "duplicate_license_id_count": 0,
            "conflicting_license_id_count": 0,
            "licenses_missing_expiry_date": 0,
            "licenses_missing_facility_id": 0,
            "duplicate_request_id_count": 0,
            "conflicting_request_id_count": 0,
            "requests_missing_facility_id": 0,
            "latest_request_date": None,
        }
    return dict(zip(columns, rows[0]))


def build_nafes_data_note(quality: dict[str, Any], language: str = "en") -> str:
    latest = quality.get("latest_request_date")
    if isinstance(latest, datetime):
        latest = latest.date()
    elif isinstance(latest, str) and latest:
        latest = date.fromisoformat(latest[:10])

    if latest is None:
        days_old = None
        freshness = (
            "Warning: no request date is available to assess data freshness."
            if language == "en"
            else "تحذير: لا يتوفر تاريخ طلب لتقييم حداثة البيانات."
        )
    else:
        days_old = (datetime.now(timezone.utc).date() - latest).days
        label = "تحذير" if language == "ar" else "Warning"
        latest_text = latest.isoformat()
        if days_old > NAFES_STALE_REQUEST_DAYS:
            freshness = (
                f"{label}: latest request data is {latest_text} (UTC), {days_old} days old; "
                f"this exceeds the {NAFES_STALE_REQUEST_DAYS}-day freshness threshold."
                if language == "en"
                else f"{label}: أحدث بيانات الطلبات بتاريخ {latest_text} (UTC)، قبل {days_old} يومًا؛ "
                f"وهذا يتجاوز حد الحداثة البالغ {NAFES_STALE_REQUEST_DAYS} يومًا."
            )
        else:
            freshness = (
                f"Request data is current through {latest_text} (UTC), {days_old} days old."
                if language == "en"
                else f"بيانات الطلبات محدثة حتى {latest_text} (UTC)، قبل {days_old} يومًا."
            )

    notes = [freshness]
    if quality.get("duplicate_license_id_count"):
        count = int(quality["duplicate_license_id_count"])
        notes.append(
            f"The source contains duplicate rows for {count} license IDs; license counts are deduplicated by License_ID."
            if language == "en"
            else f"يحتوي المصدر على سجلات مكررة لـ {count} من معرفات الرخص؛ وتُحسب الرخص الفريدة باستخدام License_ID."
        )
    if quality.get("duplicate_request_id_count"):
        count = int(quality["duplicate_request_id_count"])
        notes.append(
            f"The source contains duplicate rows for {count} request IDs; request counts are deduplicated by Request_ID."
            if language == "en"
            else f"يحتوي المصدر على سجلات مكررة لـ {count} من معرفات الطلبات؛ وتُحسب الطلبات الفريدة باستخدام Request_ID."
        )
    if quality.get("conflicting_license_id_count"):
        count = int(quality["conflicting_license_id_count"])
        notes.append(
            f"{count} license IDs have conflicting duplicate values; treat their facility/expiry details as unresolved."
            if language == "en"
            else f"يوجد {count} من معرفات الرخص ذات سجلات مكررة متعارضة؛ تُعد تفاصيل المنشأة أو الانتهاء غير محسومة."
        )
    if quality.get("licenses_missing_expiry_date"):
        count = int(quality["licenses_missing_expiry_date"])
        notes.append(
            f"{count} unique licenses have no expiry date and are excluded from date-window results."
            if language == "en"
            else f"يوجد {count} من الرخص الفريدة دون تاريخ انتهاء، واستُبعدت من نتائج النطاق الزمني."
        )
    if quality.get("conflicting_request_id_count"):
        count = int(quality["conflicting_request_id_count"])
        notes.append(
            f"{count} request IDs have conflicting duplicate values."
            if language == "en"
            else f"يوجد {count} من معرفات الطلبات ذات سجلات مكررة متعارضة."
        )
    if quality.get("licenses_missing_facility_id"):
        count = int(quality["licenses_missing_facility_id"])
        notes.append(
            f"{count} license records have no facility ID and cannot be shown in facility-level results."
            if language == "en"
            else f"يوجد {count} من سجلات الرخص دون معرف منشأة، ولا يمكن عرضها ضمن نتائج مستوى المنشأة."
        )
    if quality.get("requests_missing_facility_id"):
        count = int(quality["requests_missing_facility_id"])
        notes.append(
            f"{count} request records have no facility ID and cannot be associated with a license facility."
            if language == "en"
            else f"يوجد {count} من سجلات الطلبات دون معرف منشأة، ولا يمكن ربطها بمنشأة رخصة."
        )
    notes.append(
        "Request statuses are shown as recorded; no status is classified as pending."
        if language == "en"
        else "تُعرض حالات الطلبات كما وردت في المصدر، دون تصنيف أي حالة على أنها قيد الانتظار."
    )
    return "\n\n".join(notes)
