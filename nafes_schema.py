import os

NAFES_PROJECT = "mos-gcp-host-dev-prj"
NAFES_DATASET = "dw_ms_crm_onprem_views"
LICENSE_VIEW_NAME = "nafes_licenses_view_01"
REQUEST_VIEW_NAME = "nafes_requests_view_01"
LICENSE_VIEW = f"{NAFES_PROJECT}.{NAFES_DATASET}.{LICENSE_VIEW_NAME}"
REQUEST_VIEW = f"{NAFES_PROJECT}.{NAFES_DATASET}.{REQUEST_VIEW_NAME}"

NAFES_EXPIRY_WINDOW_DAYS = int(os.getenv("NAFES_EXPIRY_WINDOW_DAYS", "90"))
NAFES_STALE_REQUEST_DAYS = int(os.getenv("NAFES_STALE_REQUEST_DAYS", "30"))
NAFES_MAX_BYTES_BILLED = int(os.getenv("NAFES_MAX_BYTES_BILLED", "700000000"))
NAFES_MAX_ROWS = int(os.getenv("NAFES_MAX_ROWS", "100"))

LICENSE_INTERNAL_COLUMNS = frozenset({"license_id"})
REQUEST_INTERNAL_COLUMNS = frozenset({"request_id"})

LICENSE_APPROVED_COLUMNS = frozenset({
    "facility_id",
    "ntw_expirydate",
    "license_status_name",
    "region_name",
    "city_name",
    "license_type_name",
    "facility_type_name",
        "license_id",
})
REQUEST_APPROVED_COLUMNS = frozenset({
    "facilityid",
    "request_date",
    "request_status_name",
    "region_name",
    "city_name",
    "request_type",
    "request_type_ar",
    "facility_type_name",
})

APPROVED_COLUMNS_BY_VIEW = {
    LICENSE_VIEW_NAME: LICENSE_APPROVED_COLUMNS | LICENSE_INTERNAL_COLUMNS,
    REQUEST_VIEW_NAME: REQUEST_APPROVED_COLUMNS | REQUEST_INTERNAL_COLUMNS,
}

SENSITIVE_COLUMNS = frozenset({
    "license_no",
    "license_code",
    "cr_number",
    "cr_name",
    "cr_expiry_date",
    "cr_owner",
    "contact_id",
        "expiry_date_future", "missing_expiry_date",
    "account_id",
    "account_name",
    "telephone1",
    "telephone2",
    "facility_email",
    "ntw_street",
    "ntw_district",
    "ntw_pobox",
    "ntw_postalcode",
    "latitude",
    "longitude",
    "request_no",
})

ALLOWED_RESULT_ALIASES = frozenset({
    "facility_id",
    "unique_license_count",
    "license_expiry_date",
    "license_source_status",
    "license_type_variants",
    "license_type_name",
    "calculated_expiry_status",
    "conflicting_license_count",
    "associated_request_count",
    "request_status",
    "latest_request_date",
    "days_since_latest_request",
    "data_freshness_warning",
    "region_name",
    "city_name",
    "license_type_name",
    "facility_type_name",
    "days_to_expiry",
    "days_since_latest_request",
    "duplicate_license_id_count",
    "conflicting_license_id_count",
    "duplicate_request_id_count",
    "conflicting_request_id_count",
    "licenses_missing_facility_id",
    "licenses_missing_expiry_date",
    "requests_missing_facility_id",
    "freshness_threshold_days",
})

ALLOWED_DERIVED_COLUMNS = frozenset({
    "_license_id",
    "_request_id",
    "facility_id_variants",
    "expiry_date_variants",
    "license_status_variants",
    "license_type_variants",
    "request_status_variants",
    "request_date_variants",
    "license_row_count",
    "request_row_count",
    "missing_facility_rows",
    "missing_expiry_rows",
    "license_source_status",
    "license_expiry_date",
    "request_status",
    "latest_request_date",
})


def get_nafes_schema_prompt() -> str:
    license_columns = sorted(LICENSE_APPROVED_COLUMNS | LICENSE_INTERNAL_COLUMNS)
    request_columns = sorted(REQUEST_APPROVED_COLUMNS | REQUEST_INTERNAL_COLUMNS)
    return f"""
Approved BigQuery views and columns:
`{LICENSE_VIEW}`: {", ".join(license_columns)}
`{REQUEST_VIEW}`: {", ".join(request_columns)}

Use only these two fully-qualified views and listed columns. Do not use SELECT *.
License_ID may be returned only as `license_id` for a license-list question;
carry it as _license_id from license_by_id. Otherwise use it only for distinct
counts. Request_ID is internal-only: carry it as _request_id in request_by_id
or use it in COUNT(DISTINCT ...), never return it.
license_by_id must group by License_ID and calculate facility_id_variants,
expiry_date_variants, and license_status_variants. Create unambiguous_licenses
filtered to exactly one value for each variant count before detail aggregation.
request_by_id must group by Request_ID and calculate facility_id_variants,
request_status_variants, and request_date_variants. Create unambiguous_requests
filtered to exactly one value for each variant count before request aggregation.
Aggregate each view by Facility_ID/FacilityId before joining; never join raw rows.
Exclude blank or missing facility IDs from facility-level joins and call out how
many records cannot be associated because the facility ID is missing.
The only relationship between a license and a request is facility-level association.
Use the wording "associated facility requests"; do not claim that a request created,
renewed, or modified a particular license.
Keep License_Status_Name as its recorded source status. Calculate expiry status
separately with CURRENT_DATE('UTC'); do not infer business meanings from statuses.
Use @facility_id for a facility-specific filter and @window_days for expiry windows.
The default expiry window is {NAFES_EXPIRY_WINDOW_DAYS} days.
For request freshness, use MAX(Request_Date) dynamically and compare it with
CURRENT_DATE('UTC'); flag stale data older than {NAFES_STALE_REQUEST_DAYS} days.
Use @freshness_threshold_days for the configurable freshness threshold.
Only answer these use cases: distinct license count, licenses expiring within a
window, activity for a facility ID, facilities with expiring licenses and associated
requests, and requests grouped by their recorded status.
""".strip()
