import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from google.cloud import bigquery
import app as application
from bigquery_database import execute_sql_with_parameters
from config import BIGQUERY_DATASET_ID, BIGQUERY_PROJECT_ID, BIGQUERY_TABLE_ID
from gemini_service import generate_nafes_answer, generate_nafes_sql
from nafes_service import (
    _DATA_QUALITY_SQL,
    build_nafes_data_note,
    execute_nafes_sql,
    extract_facility_id,
    extract_window_days,
    get_nafes_data_quality,
    is_nafes_question,
    redact_facility_id,
)
from nafes_schema import (
    APPROVED_COLUMNS_BY_VIEW,
    LICENSE_VIEW,
    REQUEST_VIEW,
    SENSITIVE_COLUMNS,
    get_nafes_schema_prompt,
)
from sql_guardrails import OutOfScopeQueryError, validate_nafes_sql, validate_sql


def license_ctes(license_table):
    return f"""
    WITH license_by_id AS (
      SELECT License_ID AS _license_id,
             IF(COUNT(DISTINCT IFNULL(TRIM(Facility_ID), '')) = 1, ANY_VALUE(Facility_ID), NULL) AS facility_id,
             IF(COUNT(DISTINCT IFNULL(CAST(ntw_expiryDate AS STRING), '')) = 1, ANY_VALUE(ntw_expiryDate), NULL) AS license_expiry_date,
             IF(COUNT(DISTINCT IFNULL(License_Status_Name, '')) = 1, ANY_VALUE(License_Status_Name), NULL) AS license_source_status,
             IF(COUNT(DISTINCT IFNULL(License_Type_Name, '')) = 1, ANY_VALUE(License_Type_Name), NULL) AS license_type_name,
             COUNT(DISTINCT IFNULL(TRIM(Facility_ID), '')) AS facility_id_variants,
             COUNT(DISTINCT IFNULL(CAST(ntw_expiryDate AS STRING), '')) AS expiry_date_variants,
             COUNT(DISTINCT IFNULL(License_Status_Name, '')) AS license_status_variants,
             COUNT(DISTINCT IFNULL(License_Type_Name, '')) AS license_type_variants
      FROM {license_table}
      WHERE License_ID IS NOT NULL
      GROUP BY License_ID
    ), unambiguous_licenses AS (
          SELECT _license_id, facility_id, license_expiry_date, license_source_status,
              license_type_name, facility_id_variants, expiry_date_variants,
              license_status_variants, license_type_variants
      FROM license_by_id
      WHERE facility_id_variants = 1 AND expiry_date_variants = 1
        AND license_status_variants = 1
        AND license_type_variants = 1
    )
    """


def request_ctes(request_table):
    return f"""
    request_by_id AS (
      SELECT Request_ID AS _request_id,
             IF(COUNT(DISTINCT IFNULL(TRIM(FacilityId), '')) = 1, ANY_VALUE(FacilityId), NULL) AS facility_id,
             IF(COUNT(DISTINCT IFNULL(Request_Status_Name, '')) = 1, ANY_VALUE(Request_Status_Name), NULL) AS request_status,
             IF(COUNT(DISTINCT IFNULL(CAST(Request_Date AS STRING), '')) = 1, ANY_VALUE(Request_Date), NULL) AS request_date,
             COUNT(DISTINCT IFNULL(TRIM(FacilityId), '')) AS facility_id_variants,
             COUNT(DISTINCT IFNULL(Request_Status_Name, '')) AS request_status_variants,
             COUNT(DISTINCT IFNULL(CAST(Request_Date AS STRING), '')) AS request_date_variants
      FROM {request_table}
      WHERE Request_ID IS NOT NULL
      GROUP BY Request_ID
    ), unambiguous_requests AS (
      SELECT _request_id, facility_id, request_status, request_date,
             facility_id_variants, request_status_variants, request_date_variants
      FROM request_by_id
      WHERE facility_id_variants = 1 AND request_status_variants = 1
        AND request_date_variants = 1
    )
    """


class NafesMetadataTests(unittest.TestCase):
    def test_approved_schema_excludes_sensitive_columns(self):
        approved = set().union(*APPROVED_COLUMNS_BY_VIEW.values())
        self.assertFalse(approved & SENSITIVE_COLUMNS)
        prompt = get_nafes_schema_prompt().lower()
        self.assertIn(LICENSE_VIEW.lower(), prompt)
        self.assertIn(REQUEST_VIEW.lower(), prompt)
        self.assertNotIn("contact_name", prompt)
        self.assertNotIn("telephone1", prompt)
        self.assertNotIn("latitude", prompt)
        self.assertIn("count(distinct", prompt)
        self.assertIn("current_date('utc')", prompt)


class ParameterizedBigQueryTests(unittest.TestCase):
    def test_parameters_and_cost_cap_are_used_for_dry_run_and_execution(self):
        dry_run = MagicMock(total_bytes_processed=100)
        result = MagicMock()
        result.schema = [SimpleNamespace(name="count")]
        result.__iter__.return_value = iter([{"count": 3}])
        execution = MagicMock()
        execution.result.return_value = result
        parameter = bigquery.ScalarQueryParameter("facility_id", "STRING", "FAC-1")
        with patch(
            "bigquery_database.client.query",
            side_effect=[dry_run, execution],
        ) as query:
            columns, rows = execute_sql_with_parameters(
                "SELECT @facility_id AS count",
                [parameter],
                maximum_bytes_billed=200,
            )
        self.assertEqual(columns, ["count"])
        self.assertEqual(rows, [(3,)])
        self.assertEqual(query.call_count, 2)
        self.assertTrue(query.call_args_list[0].kwargs["job_config"].dry_run)
        self.assertEqual(query.call_args_list[0].kwargs["job_config"].query_parameters, [parameter])
        self.assertEqual(query.call_args_list[1].kwargs["job_config"].maximum_bytes_billed, 200)

    def test_dry_run_refuses_over_budget_before_query_execution(self):
        with patch("bigquery_database.client.query", return_value=MagicMock(total_bytes_processed=201)) as query:
            with self.assertRaisesRegex(ValueError, "processing limit"):
                execute_sql_with_parameters(
                    "SELECT 1",
                    [],
                    maximum_bytes_billed=200,
                )
        query.assert_called_once()


class NafesSqlGuardrailTests(unittest.TestCase):
    license_table = f"`{LICENSE_VIEW}`"
    request_table = f"`{REQUEST_VIEW}`"

    def assert_rejected(self, sql, error_type=ValueError, **kwargs):
        with self.assertRaises(error_type):
            validate_nafes_sql(sql, **kwargs)

    def test_accepts_unique_license_count_and_applies_row_limit(self):
        sql = (
            f"SELECT COUNT(DISTINCT l.License_ID) AS unique_license_count "
            f"FROM {self.license_table} AS l"
        )
        safe = validate_nafes_sql(sql)
        self.assertTrue(safe.endswith("LIMIT 100"))

    def test_accepts_dedup_ctes_and_aggregate_join(self):
        sql = f"""{license_ctes(self.license_table)},
        {request_ctes(self.request_table)},
        licenses_by_facility AS (
          SELECT facility_id, COUNT(*) AS unique_license_count
                    FROM unambiguous_licenses
                    WHERE facility_id IS NOT NULL AND TRIM(facility_id) != ''
                    GROUP BY facility_id
        ), requests_by_facility AS (
          SELECT facility_id, COUNT(*) AS associated_request_count
                    FROM unambiguous_requests
                    WHERE facility_id IS NOT NULL AND TRIM(facility_id) != ''
                    GROUP BY facility_id
        )
        SELECT l.facility_id AS facility_id,
               l.unique_license_count AS unique_license_count,
               r.associated_request_count AS associated_request_count
        FROM licenses_by_facility AS l
        LEFT JOIN requests_by_facility AS r ON l.facility_id = r.facility_id
        """
        safe = validate_nafes_sql(sql)
        self.assertIn("LEFT JOIN", safe)
        self.assertIn("LIMIT 100", safe)

    def test_rejects_detail_query_without_dedup_and_conflict_filters(self):
        sql = (
            f"SELECT l.Facility_ID AS facility_id, "
            f"COUNT(DISTINCT l.License_ID) AS unique_license_count "
            f"FROM {self.license_table} AS l GROUP BY l.Facility_ID"
        )
        self.assert_rejected(sql)

    def test_rejects_fabricated_duplicate_variant_counts(self):
        sql = f"""{license_ctes(self.license_table)}
        SELECT facility_id AS facility_id, COUNT(*) AS unique_license_count
        FROM unambiguous_licenses GROUP BY facility_id
        """
        fabricated = sql.replace(
            "COUNT(DISTINCT IFNULL(CAST(ntw_expiryDate AS STRING), '')) AS expiry_date_variants",
            "1 AS expiry_date_variants",
        )
        self.assert_rejected(fabricated)

    def test_rejects_unapproved_table_and_dataset(self):
        self.assert_rejected(
            "SELECT x.Facility_ID AS facility_id FROM `other.project.view` AS x",
            OutOfScopeQueryError,
        )
        self.assert_rejected(
            "SELECT x.Facility_ID AS facility_id FROM `mos-gcp-host-dev-prj.other.nafes_licenses_view_01` AS x",
            OutOfScopeQueryError,
        )

    def test_rejects_unqualified_or_wrong_view_columns(self):
        self.assert_rejected(
            f"SELECT l.Request_Status_Name AS request_status FROM {self.license_table} AS l",
            OutOfScopeQueryError,
        )
        self.assert_rejected(
            f"SELECT Contact_Name AS contact_name FROM {self.request_table}",
            OutOfScopeQueryError,
        )

    def test_rejects_sensitive_columns_and_result_aliases(self):
        for column in ("Contact_Name", "Telephone1", "Facility_Email", "CR_Number", "Latitude"):
            with self.subTest(column=column):
                self.assert_rejected(
                    f"SELECT r.{column} AS request_status FROM {self.request_table} AS r",
                    OutOfScopeQueryError,
                )
        self.assert_rejected(
            f"SELECT l.Facility_ID AS facility_id, l.License_ID AS license_id FROM {self.license_table} AS l",
            OutOfScopeQueryError,
        )

    def test_rejects_wildcards_unauthorized_aliases_and_multiple_statements(self):
        self.assert_rejected(
            f"SELECT l.* FROM {self.license_table} AS l",
            OutOfScopeQueryError,
        )
        self.assert_rejected(
            f"SELECT l.Facility_ID AS arbitrary_output FROM {self.license_table} AS l",
            OutOfScopeQueryError,
        )
        self.assert_rejected(
            f"SELECT l.Facility_ID AS facility_id FROM {self.license_table} AS l; DELETE FROM `x.y.z`",
        )
        self.assert_rejected("DELETE FROM `x.y.z` WHERE 1=1")

    def test_request_identifier_is_not_returned(self):
        self.assert_rejected(
            f"SELECT r.Request_ID AS request_status FROM {self.request_table} AS r",
            OutOfScopeQueryError,
        )
        safe = validate_nafes_sql(
            f"SELECT COUNT(DISTINCT r.Request_ID) AS associated_request_count FROM {self.request_table} AS r"
        )
        self.assertIn("COUNT(DISTINCT", safe)

    def test_facility_filter_must_be_parameterized(self):
        sql = f"""{license_ctes(self.license_table)}
        SELECT facility_id AS facility_id, COUNT(*) AS unique_license_count
        FROM unambiguous_licenses
        WHERE facility_id = @facility_id GROUP BY facility_id
        """
        safe = validate_nafes_sql(sql, required_parameters={"facility_id"})
        self.assertIn("@facility_id", safe)
        self.assert_rejected(
            sql.replace("@facility_id", "'facility-12345678'"),
            required_parameters={"facility_id"},
        )
        self.assert_rejected(sql, required_parameters={"window_days"})

    def test_expiry_filter_requires_configured_window_and_utc(self):
        sql = f"""{license_ctes(self.license_table)}
        SELECT facility_id AS facility_id, COUNT(*) AS unique_license_count
        FROM unambiguous_licenses
        WHERE license_expiry_date BETWEEN CURRENT_DATE('UTC')
          AND DATE_ADD(CURRENT_DATE('UTC'), INTERVAL @window_days DAY)
        GROUP BY facility_id
        """
        safe = validate_nafes_sql(sql, required_parameters={"window_days"})
        self.assertIn("CURRENT_DATE('UTC')", safe)
        self.assert_rejected(sql.replace("@window_days", "90"))
        self.assert_rejected(sql.replace("CURRENT_DATE('UTC')", "CURRENT_DATE()"))

    def test_accepts_bounded_license_expiry_listing_with_safe_license_id(self):
        sql = f"""{license_ctes(self.license_table)}
        SELECT facility_id AS facility_id,
               _license_id AS license_id,
               license_type_name AS license_type_name,
               license_expiry_date AS license_expiry_date,
               CASE
                 WHEN license_expiry_date < CURRENT_DATE('UTC') THEN 'Expired'
                 WHEN license_expiry_date <= DATE_ADD(CURRENT_DATE('UTC'), INTERVAL @window_days DAY) THEN 'Expiring Soon'
                 ELSE 'Not Expired'
               END AS calculated_expiry_status
        FROM unambiguous_licenses
        WHERE license_expiry_date >= CURRENT_DATE('UTC')
          AND license_expiry_date <= DATE_ADD(CURRENT_DATE('UTC'), INTERVAL @window_days DAY)
          AND license_type_variants = 1
        ORDER BY license_expiry_date, license_id
        LIMIT 100
        """
        safe = validate_nafes_sql(sql, required_parameters={"window_days"})
        for field in ("facility_id", "license_id", "license_type_name", "license_expiry_date"):
            self.assertIn(field, safe)

    def test_result_allowlist_permits_license_id_only_as_display_alias(self):
        from nafes_service import _validate_result

        _validate_result(
            ["facility_id", "license_id", "license_type_name", "license_expiry_date"],
            [("FAC-1", "LIC-1", "Facility", date(2026, 12, 1))],
        )
        with self.assertRaisesRegex(ValueError, "non-approved"):
            _validate_result(["request_id"], [("REQ-1",)])

    def test_only_approved_parameters_are_allowed(self):
        sql = (
            f"SELECT COUNT(DISTINCT l.License_ID) AS unique_license_count "
            f"FROM {self.license_table} AS l WHERE l.Facility_ID = @user_value"
        )
        self.assert_rejected(sql)

    def test_existing_v3_validator_contract_is_unchanged(self):
        v3_sql = (
            f"SELECT sport FROM `{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}`"
        )
        with patch("sql_guardrails.get_allowed_columns", return_value={"sport"}):
            self.assertTrue(validate_sql(v3_sql).endswith("LIMIT 100"))
            self.assertNotIn("LIMIT", validate_sql(v3_sql, row_limit=None))


class NafesServiceTests(unittest.TestCase):
    def test_count_and_listing_phrasings_are_nafes_questions(self):
        self.assertTrue(is_nafes_question("How many facility licenses will expire in the next 90 days?"))
        self.assertTrue(is_nafes_question("Show the facility licenses expiring within the next 90 days."))

    def test_question_routing_facility_id_redaction_and_window(self):
        question = "Show activity for facility ID FAC-123456 in the next 45 days"
        self.assertTrue(is_nafes_question(question))
        self.assertEqual(extract_facility_id(question), "FAC-123456")
        self.assertNotIn("FAC-123456", redact_facility_id(question))
        self.assertEqual(extract_window_days(question), 45)
        self.assertEqual(extract_window_days("How many unique facility licenses?"), 90)
        with self.assertRaises(ValueError):
            extract_window_days("expire in 3651 days")

    def test_execute_nafes_sql_binds_facility_id_and_checks_result_columns(self):
        sql = f"""{license_ctes(f"`{LICENSE_VIEW}`")}
        SELECT facility_id AS facility_id, COUNT(*) AS unique_license_count
        FROM unambiguous_licenses
        WHERE facility_id = @facility_id
        GROUP BY facility_id
        """
        with patch(
            "nafes_service.execute_sql_with_parameters",
            return_value=(
                ["facility_id", "unique_license_count"],
                [("FAC-123456", 2)],
            ),
        ) as execute:
            safe_sql, columns, rows = execute_nafes_sql(
                sql,
                "Show activity for facility ID FAC-123456",
            )
        self.assertIn("@facility_id", safe_sql)
        self.assertEqual(columns, ["facility_id", "unique_license_count"])
        self.assertEqual(rows, [("FAC-123456", 2)])
        parameters = execute.call_args.args[1]
        self.assertEqual([(parameter.name, parameter.value) for parameter in parameters], [("facility_id", "FAC-123456")])

    def test_result_validation_rejects_sensitive_columns(self):
        with patch(
            "nafes_service.execute_sql_with_parameters",
            return_value=(["Contact_Name"], [("private",)]),
        ):
            with self.assertRaisesRegex(ValueError, "non-approved"):
                execute_nafes_sql(
                    f"SELECT COUNT(DISTINCT l.License_ID) AS unique_license_count FROM `{LICENSE_VIEW}` AS l",
                    "How many unique facility licenses are available?",
                )

    def test_data_quality_query_groups_by_ids_and_reports_conflicts(self):
        from nafes_service import _DATA_QUALITY_SQL

        safe = validate_nafes_sql(
            _DATA_QUALITY_SQL,
            row_limit=1,
            enforce_deduplication=False,
        )
        self.assertIn("GROUP BY License_ID", safe)
        self.assertIn("GROUP BY Request_ID", safe)
        self.assertIn("expiry_date_variants", safe)
        self.assertIn("licenses_missing_expiry_date", safe)
        self.assertIn("request_status_variants", safe)
        self.assertNotIn("Contact_Name", safe)

    def test_data_quality_snapshot_is_read_only_and_returns_metrics(self):
        fields = [
            "duplicate_license_id_count",
            "conflicting_license_id_count",
            "licenses_missing_expiry_date",
            "licenses_missing_facility_id",
            "duplicate_request_id_count",
            "conflicting_request_id_count",
            "requests_missing_facility_id",
            "latest_request_date",
        ]
        values = (1059, 0, 3, 0, 0, 0, 230, date(2026, 1, 30))
        with patch("nafes_service.execute_sql_with_parameters", return_value=(fields, [values])) as execute:
            quality = get_nafes_data_quality()
        self.assertEqual(quality["duplicate_license_id_count"], 1059)
        self.assertEqual(quality["licenses_missing_expiry_date"], 3)
        self.assertEqual(quality["requests_missing_facility_id"], 230)
        self.assertEqual(quality["latest_request_date"], date(2026, 1, 30))
        self.assertIn("SELECT", execute.call_args.args[0].upper())
        self.assertNotRegex(execute.call_args.args[0], r"(?i)\b(insert|update|delete|merge|drop|alter)\b")

    def test_freshness_note_warns_when_request_data_is_stale(self):
        stale_date = date.today() - timedelta(days=31)
        note = build_nafes_data_note(
            {
                "latest_request_date": stale_date,
                "duplicate_license_id_count": 1059,
                "conflicting_license_id_count": 2,
                "requests_missing_facility_id": 3,
            },
            "en",
        )
        self.assertIn("Warning", note)
        self.assertIn("UTC", note)
        self.assertIn("deduplicated by License_ID", note)
        self.assertIn("conflicting duplicate", note)
        self.assertIn("no facility ID", note)
        self.assertIn("no status is classified as pending", note)

    def test_freshness_note_has_arabic_copy(self):
        note = build_nafes_data_note({"latest_request_date": date.today()}, "ar")
        self.assertIn("UTC", note)
        self.assertIn("حالات الطلبات", note)


class NafesGeminiTests(unittest.TestCase):
    def test_sql_prompt_uses_only_safe_schema_and_requires_ctes(self):
        result = SimpleNamespace(text="SELECT COUNT(DISTINCT License_ID) AS unique_license_count")
        with patch("gemini_service.client.models.generate_content", return_value=result) as generate:
            sql = generate_nafes_sql("How many unique facility licenses?", "en")
        prompt = generate.call_args.kwargs["contents"].lower()
        self.assertIn(LICENSE_VIEW.lower(), prompt)
        self.assertIn(REQUEST_VIEW.lower(), prompt)
        self.assertNotIn("contact_name", prompt)
        self.assertIn("unambiguous_licenses", prompt)
        self.assertIn("unambiguous_requests", prompt)
        self.assertEqual(sql, result.text)

    def test_answer_prompt_preserves_status_and_relationship_boundaries(self):
        result = SimpleNamespace(text="License follow-up summary")
        with patch("gemini_service.client.models.generate_content", return_value=result) as generate:
            answer = generate_nafes_answer(
                "Show license activity",
                ["facility_id", "license_source_status"],
                [("FAC-1", "فعالة")],
                "en",
            )
        prompt = generate.call_args.kwargs["contents"].lower()
        self.assertIn("do not infer", prompt)
        self.assertIn("associated facility requests", prompt)
        self.assertNotIn("telephone", prompt.split("approved results:")[1])
        self.assertEqual(answer, result.text)


class NafesChatIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(application.app)

    def nafes_patches(self):
        return (
            patch("app.is_nafes_question", return_value=True),
            patch("app.generate_nafes_sql", return_value="SELECT safe Nafes SQL"),
            patch(
                "app.execute_nafes_sql",
                return_value=("SELECT safe Nafes SQL", ["facility_id", "unique_license_count"], [("FAC-1", 2)]),
            ),
            patch("app.get_nafes_data_quality", return_value={"latest_request_date": date.today()}),
            patch("app.generate_nafes_answer", return_value="License summary"),
            patch("app.generate_sql"),
        )

    def test_json_ask_uses_existing_endpoint_and_skips_v3_sql_path(self):
        patches = self.nafes_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as v3_generate:
            response = self.client.post(
                "/ask",
                json={"question": "How many unique facility licenses are available?", "language": "en"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("License summary", response.json()["answer"])
        self.assertIn("Request data is current through", response.json()["answer"])
        v3_generate.assert_not_called()

    def test_expiry_count_question_returns_expected_count(self):
        question = "How many facility licenses will expire in the next 90 days?"
        with (
            patch("app.generate_nafes_sql", return_value="count SQL"),
            patch("app.execute_nafes_sql", return_value=("count SQL", ["unique_license_count"], [(1317,)])),
            patch("app.get_nafes_data_quality", return_value={"latest_request_date": date.today()}),
            patch("app.generate_nafes_answer", return_value="1,317 unique licenses expire in the next 90 days."),
            patch("app.generate_sql") as v3_generate,
        ):
            response = self.client.post("/ask", json={"question": question, "language": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("1,317", response.json()["answer"])
        v3_generate.assert_not_called()

    def test_expiry_listing_question_returns_bounded_license_details(self):
        question = "Show the facility licenses expiring within the next 90 days."
        columns = [
            "facility_id",
            "license_id",
            "license_type_name",
            "license_expiry_date",
            "calculated_expiry_status",
        ]
        rows = [("FAC-1", "LIC-1", "Club license", date(2026, 11, 1), "Expiring Soon")]
        with (
            patch("app.generate_nafes_sql", return_value="bounded listing SQL") as generate_nafes,
            patch("app.execute_nafes_sql", return_value=("bounded listing SQL", columns, rows)) as execute_nafes,
            patch("app.get_nafes_data_quality", return_value={"latest_request_date": date.today()}),
            patch("app.generate_nafes_answer", return_value="Facility FAC-1 has one expiring license.") as answer,
            patch("app.generate_sql") as v3_generate,
        ):
            response = self.client.post("/ask", json={"question": question, "language": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("FAC-1", response.json()["answer"])
        generate_nafes.assert_called_once_with(question, "en")
        execute_nafes.assert_called_once_with("bounded listing SQL", question)
        self.assertEqual(answer.call_args.args[1], columns)
        self.assertEqual(answer.call_args.args[2], rows)
        v3_generate.assert_not_called()

    def test_streaming_ask_returns_summary_and_freshness_without_export_context(self):
        patches = self.nafes_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as v3_generate:
            response = self.client.post(
                "/ask/stream",
                json={"question": "How many unique facility licenses are available?", "language": "en"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("License summary", response.text)
        self.assertIn("Request data is current through", response.text)
        self.assertNotIn("event: context", response.text)
        v3_generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
