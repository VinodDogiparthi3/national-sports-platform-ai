import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
from schema_metadata import get_bigquery_schema
from sql_guardrails import OutOfScopeQueryError, validate_sql
from view_catalog import APPROVED_VIEWS, find_view, get_view, is_join_verified
from view_router import build_routing_prompt, parse_route, route_question

PROJECT = "mos-gcp-host-dev-prj"
NO_MATCH = "no match"


def fq(view_id):
    return f"`{get_view(view_id).full_id}`"


class CatalogTests(unittest.TestCase):
    def test_has_21_unique_views_in_3_datasets(self):
        self.assertEqual(len(APPROVED_VIEWS), 21)
        self.assertEqual(len({v.view for v in APPROVED_VIEWS}), 21)
        self.assertEqual(
            {v.dataset for v in APPROVED_VIEWS},
            {"dw_nsp_api_views", "dw_ms_crm_onprem_views", "dw_sharepoint_views"},
        )

    def test_find_view_requires_matching_dataset_and_project(self):
        self.assertIsNotNone(find_view(PROJECT, "dw_sharepoint_views", "budget_view_01"))
        self.assertIsNone(find_view(PROJECT, "dw_nsp_api_views", "budget_view_01"))
        self.assertIsNone(find_view("other", "dw_sharepoint_views", "budget_view_01"))

    def test_no_join_is_verified_by_default(self):
        self.assertTrue(is_join_verified({"budget_view_01"}))
        self.assertFalse(is_join_verified({"budget_view_01", "sopc_view_01"}))

    def test_sports_medicine_descriptions_use_verified_schema_differences(self):
        view_one = get_view("sports_medicine_view_01")
        view_two = get_view("sports_medicine_view_02")

        self.assertIn("actual and forecast fields are STRING", view_one.description)
        self.assertIn("no source_file or sheet_name fields", view_one.description)
        self.assertIn("actual and forecast fields are NUMERIC", view_two.description)
        self.assertIn("source_file and sheet_name are available", view_two.description)
        prompt = build_routing_prompt("Show the forecast from sports medicine", "en", {})
        self.assertIn(view_one.description, prompt)
        self.assertIn(view_two.description, prompt)

    def test_age_group_view_has_verified_reporting_year(self):
        self.assertEqual(
            get_view("vw_health_determinants_statistics_publication").available_years,
            (2023,),
        )

    def test_population_schema_documents_total_row_semantics(self):
        fields = [("age_group", "STRING", "NULLABLE"), ("total_2024_final_total", "STRING", "NULLABLE")]
        with patch("schema_metadata.get_view_columns", return_value=fields):
            schema = get_bigquery_schema("population_estimates_statistics_view_01")

        self.assertIn("filter to `age_group` = 'Total'", schema)
        self.assertIn("exclude `age_group` = 'Total'", schema)


class RouterParsingTests(unittest.TestCase):
    def test_single_view(self):
        self.assertEqual(parse_route({"view_id": "sopc_view_01"}, NO_MATCH).view_id, "sopc_view_01")

    def test_unknown_view_is_rejected(self):
        route = parse_route({"view_id": "made_up", "clarification": "Which?"}, NO_MATCH)
        self.assertIsNone(route.view_id)
        self.assertEqual(route.clarification, "Which?")

    def test_multiple_candidates_ask_user_to_clarify(self):
        route = parse_route(
            {"view_id": None, "candidates": ["budget_view_01", "budget_control_view_01"], "clarification": "Which budget?"},
            NO_MATCH,
        )
        self.assertIsNone(route.view_id)
        self.assertIn("Budget View", route.clarification)
        self.assertIn("Budget Control View", route.clarification)

    def test_ambiguous_sports_medicine_views_are_not_silently_selected(self):
        route = parse_route(
            {
                "view_id": None,
                "candidates": ["sports_medicine_view_01", "sports_medicine_view_02"],
                "clarification": "Which sports medicine data set?",
            },
            NO_MATCH,
        )

        self.assertIsNone(route.view_id)
        self.assertIn("Sports Medicine View 01", route.clarification)
        self.assertIn("Sports Medicine View 02", route.clarification)

    def test_no_match_uses_default_message(self):
        self.assertEqual(parse_route({"view_id": None}, NO_MATCH).clarification, NO_MATCH)
        self.assertEqual(parse_route("garbage", NO_MATCH).clarification, NO_MATCH)

    def test_route_question_uses_gemini_json(self):
        reply = SimpleNamespace(text='```json\n{"view_id": "nafes_licenses_view_01"}\n```')
        with (
            patch("view_router.get_all_view_columns", return_value={}),
            patch("gemini_service.client.models.generate_content", return_value=reply) as gen,
        ):
            route = route_question("How many active licenses are available?")
        self.assertEqual(route.view_id, "nafes_licenses_view_01")
        self.assertIn("nafes_requests_view_01", gen.call_args.kwargs["contents"])

    def test_route_question_non_json_response_asks_for_clarification(self):
        with (
            patch("view_router.get_all_view_columns", return_value={}),
            patch("gemini_service.client.models.generate_content", return_value=SimpleNamespace(text="not json")),
        ):
            route = route_question("hello?")
        self.assertIsNone(route.view_id)
        self.assertTrue(route.clarification)


class RoutedSqlTests(unittest.TestCase):
    def test_routed_sql_uses_selected_view_schema(self):
        from gemini_service import generate_routed_sql

        with (
            patch("view_router.route_question", return_value=SimpleNamespace(view_id="budget_view_01", clarification=None)),
            patch("gemini_service.get_bigquery_schema", return_value="schema") as schema,
            patch("gemini_service.client.models.generate_content", return_value=SimpleNamespace(text="SELECT 1")),
        ):
            self.assertEqual(generate_routed_sql("budget in 2025?"), "SELECT 1")
        schema.assert_called_once_with("budget_view_01")

    def test_unrouted_question_returns_clarification(self):
        from gemini_service import generate_routed_sql

        with patch("view_router.route_question", return_value=SimpleNamespace(view_id=None, clarification="Which one?")):
            self.assertEqual(generate_routed_sql("hmm"), "CLARIFICATION: Which one?")


class GuardrailTests(unittest.TestCase):
    def validate(self, sql, columns):
        with patch("sql_guardrails.get_allowed_columns", return_value=columns):
            return validate_sql(sql)

    def test_every_approved_view_is_accepted(self):
        for view in APPROVED_VIEWS:
            with self.subTest(view=view.view):
                sql = self.validate(f"SELECT COUNT(*) AS n FROM `{view.full_id}`", set())
                self.assertIn(view.view, sql)

    def test_unapproved_view_and_dataset_mismatch_rejected(self):
        for sql in (
            f"SELECT 1 FROM `{PROJECT}.dw_nsp_api_views.other_view`",
            f"SELECT 1 FROM `{PROJECT}.dw_nsp_api_views.budget_view_01`",
            "SELECT 1 FROM `other.dw_nsp_api_views.nsp_base_view_01`",
        ):
            with self.subTest(sql=sql), self.assertRaises(OutOfScopeQueryError):
                self.validate(sql, set())

    def test_unverified_cross_view_join_rejected(self):
        sql = f"SELECT a.x FROM {fq('budget_view_01')} a JOIN {fq('sopc_view_01')} b ON a.x = b.x"
        with self.assertRaises(OutOfScopeQueryError):
            self.validate(sql, {"x"})

    def test_dml_ddl_rejected(self):
        for sql in (
            f"DELETE FROM {fq('budget_view_01')} WHERE TRUE",
            f"DROP TABLE {fq('budget_view_01')}",
            f"INSERT INTO {fq('budget_view_01')} (x) VALUES (1)",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                self.validate(sql, {"x"})

    def test_unknown_column_rejected_and_arabic_column_allowed(self):
        with self.assertRaises(OutOfScopeQueryError):
            self.validate(f"SELECT secret FROM {fq('sopc_view_01')}", {"year"})
        sql = self.validate(f"SELECT `السنة` FROM {fq('sopc_view_01')}", {"السنة"})
        self.assertIn("LIMIT 100", sql)


class SourceInfoTests(unittest.TestCase):
    def test_source_info_reports_dataset_and_view(self):
        info = app.get_source_info(f"SELECT COUNT(*) FROM {fq('fr_base_view_01')}")
        self.assertEqual(info["dataset"], "dw_ms_crm_onprem_views")
        self.assertEqual(info["view"], "fr_base_view_01")
        self.assertEqual(info["display_name"], "FR Base View")

    def test_source_info_none_for_unknown_tables(self):
        self.assertIsNone(app.get_source_info("SELECT 1 FROM `a.b.c`"))


class ErrorMessageTests(unittest.TestCase):
    def test_error_kinds(self):
        from google.api_core import exceptions as ex

        self.assertIn("permission", app.get_error_message(ex.Forbidden("x"), "en"))
        self.assertIn("BigQuery", app.get_error_message(ex.BadRequest("x"), "en"))
        self.assertEqual(app.get_error_message(RuntimeError("x"), "en"), "Unable to process the request.")
