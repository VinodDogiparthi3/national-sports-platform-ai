import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from gemini_service import (
    OUT_OF_SCOPE_CLARIFICATION,
    _apply_population_row_semantics,
    extract_export_analysis_question,
    generate_answer,
    generate_answer_stream,
    generate_routed_sql,
    generate_sql,
    translate_texts,
    translate_questions_to_arabic,
)
from schema_metadata import get_bigquery_schema


class GenerateSqlTests(unittest.TestCase):
    def test_actual_population_schema_cannot_supply_region_count_or_names(self):
        from gemini_service import generate_query_plan
        from sql_guardrails import OutOfScopeQueryError, validate_sql
        from view_catalog import get_view

        source_id = "population_estimates_statistics_view_01"
        question = "total no.of regions and list of regions"
        fields = [("max_year", "INTEGER", "NULLABLE"), ("age_group", "STRING", "NULLABLE")]
        for year in (2024, 2023):
            fields.extend((f"{nationality}_{year}_{gender}", "STRING", "NULLABLE")
                          for nationality in ("saudi", "non_saudi") for gender in ("female", "male", "total"))
            fields.extend((f"total_{year}_{metric}", "STRING", "NULLABLE")
                          for metric in ("female", "male", "final_total"))
        self.assertEqual(len(fields), 20)
        self.assertFalse(any("region" in name.casefold() for name, _, _ in fields))
        context = {"pending": {"kind": "views", "question": question, "options": [
            {"view_id": source_id}, {"view_id": "nsp_base_view_01"},
        ]}, "last_question": question, "view_ids": [source_id, "nsp_base_view_01"]}
        with (
            patch("schema_metadata.get_view_columns", return_value=fields),
            patch("schema_metadata.get_discovered_source", return_value=None),
            patch("schema_metadata.get_filter_values", return_value={}),
            patch("sql_guardrails.get_view_columns", return_value=fields),
            patch("gemini_service.client.models.generate_content", return_value=SimpleNamespace(
                text=f"CLARIFICATION: {OUT_OF_SCOPE_CLARIFICATION}",
            )) as model,
            patch("view_router.route_question") as route,
            patch("bigquery_database.client.query") as query,
        ):
            plan = generate_query_plan("all", conversation_context=context)
            self.assertIn("Regional breakdowns are not supported", plan.clarification)
            self.assertNotIn("nationality or region", get_view(source_id).description)
            self.assertIn("regional breakdowns are not supported", get_view(source_id).description)
            self.assertEqual(plan.clarification_source_position, 1)
            self.assertIn("Population Estimates Statistics View", plan.clarification)
            self.assertEqual(plan.conversation_context["view_ids"], context["view_ids"])
            self.assertEqual(plan.conversation_context["pending"]["question"], question)
            self.assertEqual(plan.conversation_context["pending"]["options"], context["pending"]["options"])
            self.assertEqual(plan.conversation_context["pending"]["kind"], "details")
            self.assertIn("Regional breakdowns are not supported", plan.conversation_context["pending"]["clarification"])
            self.assertEqual(plan.queries, ())
            with self.assertRaises(OutOfScopeQueryError):
                validate_sql(f"SELECT DISTINCT `region` FROM `{get_view(source_id).full_id}`")
            with self.assertRaises(OutOfScopeQueryError):
                validate_sql(f"SELECT COUNT(DISTINCT `region_name`) AS `total_regions` FROM `{get_view(source_id).full_id}`")
            model.assert_not_called()
            model.return_value = SimpleNamespace(text=(
                f"SELECT COUNT(DISTINCT `age_group`) AS `total_age_groups`, "
                f"ARRAY_AGG(DISTINCT `age_group` IGNORE NULLS) AS `age_groups` "
                f"FROM `{get_view(source_id).full_id}` WHERE `age_group` <> 'Total'"
            ))
            compatible = generate_sql("total number of age groups and list of age groups", "en", source_id)
            prompt = model.call_args.kwargs["contents"]
            self.assertIn(get_view(source_id).full_id, prompt)
            for name, data_type, mode in fields:
                self.assertIn(f"{name} {data_type} {mode}", prompt)
            self.assertIn("Do not invent columns", prompt)
            validated = validate_sql(compatible)
            self.assertIn("COUNT(DISTINCT", validated)
            self.assertIn("ARRAY_AGG", validated)
            model.assert_called_once()
        route.assert_not_called()
        query.assert_not_called()

    @patch("gemini_service.get_bigquery_schema", return_value="schema")
    @patch("gemini_service.client.models.generate_content")
    def test_removes_sql_code_fence(self, generate_content, _get_bigquery_schema):
        generate_content.return_value = SimpleNamespace(
            text="```sql\nSELECT 1\n```"
        )

        self.assertEqual(generate_sql("question"), "SELECT 1")

    @patch("schema_metadata.get_filter_values", return_value={})
    @patch("schema_metadata.get_discovered_source", return_value=None)
    @patch(
        "schema_metadata.get_view_columns",
        return_value=[("idv_classificationrecordid", "STRING", "NULLABLE"), ("contactid", "STRING", "NULLABLE")],
    )
    def test_nsp_schema_defines_athlete_count_identifier(self, _get_view_columns, _get_discovered_source, _get_filter_values):
        schema = get_bigquery_schema("nsp_base_view_01")

        self.assertIn("count distinct `idv_classificationrecordid`", schema)
        self.assertIn("Do not use `contactid`", schema)

    @patch("gemini_service.get_bigquery_schema", return_value="schema")
    @patch("gemini_service.client.models.generate_content")
    def test_prompt_specifies_out_of_scope_clarification(
        self,
        generate_content,
        _get_bigquery_schema,
    ):
        generate_content.return_value = SimpleNamespace(
            text=f"CLARIFICATION: {OUT_OF_SCOPE_CLARIFICATION}"
        )

        self.assertEqual(
            generate_sql("Tell me today's date"),
            f"CLARIFICATION: {OUT_OF_SCOPE_CLARIFICATION}",
        )
        prompt = generate_content.call_args.kwargs["contents"]
        self.assertIn(
            f"return exactly: CLARIFICATION: {OUT_OF_SCOPE_CLARIFICATION}",
            prompt,
        )
        self.assertIn("Do not describe a related request as unrelated", prompt)
        self.assertIn("ask for its name", prompt)
        self.assertIn("Directory, staff, committee, club, and affiliation requests", prompt)

    @patch("gemini_service.get_bigquery_schema", return_value="schema")
    @patch("gemini_service.client.models.generate_content")
    def test_arabic_clarification_prompt_uses_arabic(
        self,
        generate_content,
        _get_bigquery_schema,
    ):
        generate_content.return_value = SimpleNamespace(
            text="CLARIFICATION: يرجى طرح سؤال متعلق بالرياضة."
        )

        self.assertIn("CLARIFICATION", generate_sql("ما حالة الطقس؟", "ar"))
        prompt = generate_content.call_args.kwargs["contents"]
        self.assertIn("Write any clarification in Arabic", prompt)
        self.assertIn("يرجى طرح سؤال يتعلق بالمنصة الوطنية للرياضة", prompt)

    def test_answer_preserves_bigquery_float_precision_and_context_label(self):
        answer = generate_answer(
            "What was the sports event attendance rate?",
            ["average_attendance"],
            [(0.5551325036207942,)],
        )

        self.assertIn("0.5551325036207942", answer)
        self.assertIn("Average Attendance Rate", answer)

    def test_answer_displays_valid_zero_and_decimal_values(self):
        zero_answer = generate_answer("How many age groups?", ["age_groups_count"], [(0,)])
        decimal_answer = generate_answer("What is the total?", ["total"], [(Decimal("0.123456789012345678"),)])

        self.assertIn("**Age Groups:** 0", zero_answer)
        self.assertIn("0.123456789012345678", decimal_answer)

    def test_answer_uses_arabic_metric_labels(self):
        answer = generate_answer("كم عدد الرياضيين النشطين؟", ["active_athletes_count"], [(128542,)], "ar")

        self.assertIn("الرياضيون النشطون", answer)
        self.assertIn("128542", answer)
        self.assertNotIn("Total Registrations", answer)

    def test_grouped_answer_renders_every_returned_row_in_arabic(self):
        rows = [(f"منطقة {index:02d}", index) for index in range(35)]
        answer = generate_answer("عدد المؤشرات حسب المنطقة", ["region", "indicator_count"], rows, "ar")

        self.assertIn("### النتائج", answer)
        for index in range(35):
            self.assertIn(f"منطقة {index:02d}", answer)
        self.assertIn("34", answer)

    def test_arabic_grouped_headers_are_localized(self):
        answer = generate_answer(
            "كم عدد مؤشرات محددات الصحة حسب المنطقة الإدارية؟",
            ["administrative_regions", "kpi_name", "kpi_count"],
            [("Al-Jouf", "Physical activity", 2)],
            "ar",
        )

        self.assertIn("المجموعة الواردة", answer)
        self.assertIn("المؤشر", answer)
        self.assertIn("عدد المؤشرات", answer)
        self.assertIn("Al-Jouf", answer)

    def test_grouped_answer_marks_the_100_row_cap(self):
        rows = [(f"Region {index}", index) for index in range(100)]
        answer = generate_answer("Results by region", ["region", "count"], rows)
        streamed = "".join(generate_answer_stream("Results by region", ["region", "count"], rows, "ar"))

        self.assertIn("### Results (capped at 100 rows)", answer)
        self.assertIn("### النتائج (بحد أقصى 100 صف)", streamed)

    def test_age_view_unavailable_year_returns_clarification_before_sql_generation(self):
        route = SimpleNamespace(view_id="vw_health_determinants_statistics_publication", clarification=None)
        with (
            patch("view_router.route_question", return_value=route),
            patch("gemini_service.generate_sql") as generate_sql,
        ):
            result = generate_routed_sql("كم عدد الفئات العمرية لعام 2024؟", "ar")

        self.assertTrue(result.startswith("CLARIFICATION:"))
        self.assertIn("2023", result)
        generate_sql.assert_not_called()

    def test_population_total_filters_to_total_row(self):
        sql = "SELECT SUM(SAFE_CAST(`total_2024_final_total` AS INT64)) AS population FROM `project.dw_sharepoint_views.population_estimates_statistics_view_01`"
        corrected = _apply_population_row_semantics(
            sql,
            "What is the total population estimate for 2024?",
            "population_estimates_statistics_view_01",
        )

        self.assertIn("`age_group` = 'Total'", corrected)

    def test_population_age_breakdown_excludes_total_row(self):
        sql = "SELECT `age_group`, SUM(SAFE_CAST(`total_2024_final_total` AS INT64)) AS population FROM `project.dw_sharepoint_views.population_estimates_statistics_view_01` WHERE `age_group` = 'Total' GROUP BY `age_group`"
        corrected = _apply_population_row_semantics(
            sql,
            "Show the population total for each age group in 2024",
            "population_estimates_statistics_view_01",
        )

        self.assertIn("`age_group` <> 'Total'", corrected)
        self.assertNotIn("`age_group` = 'Total'", corrected)

    @patch("gemini_service.client.models.generate_content")
    def test_arabic_question_translation_preserves_names(self, generate_content):
        generate_content.return_value = SimpleNamespace(
            text='["كم عدد التسجيلات لرياضة Football؟"]'
        )

        questions = ["How many registrations are there for Football?"]
        self.assertEqual(
            translate_questions_to_arabic(questions),
            ["كم عدد التسجيلات لرياضة Football؟"],
        )
        prompt = generate_content.call_args.kwargs["contents"]
        self.assertIn("Preserve proper names", prompt)
        self.assertIn("Football", prompt)

    @patch("gemini_service.client.models.generate_content")
    def test_chat_text_translation_supports_english_and_arabic(self, generate_content):
        generate_content.return_value = SimpleNamespace(text='["Football federation"]')

        self.assertEqual(translate_texts(["اتحاد كرة القدم"], "en"), ["Football federation"])
        prompt = generate_content.call_args.kwargs["contents"]
        self.assertIn("natural English", prompt)

    @patch("gemini_service.client.models.generate_content")
    def test_export_request_with_new_query_returns_only_the_data_request(self, generate_content):
        generate_content.return_value = SimpleNamespace(
            text='{"analysis_question": "Show registrations grouped by sport and gender"}'
        )

        result = extract_export_analysis_question(
            "Show registrations grouped by sport and gender, and export them to Excel.", None
        )

        self.assertEqual(result, "Show registrations grouped by sport and gender")

    @patch("gemini_service.client.models.generate_content")
    def test_export_request_about_earlier_results_returns_none(self, generate_content):
        generate_content.return_value = SimpleNamespace(text='```json\n{"analysis_question": null}\n```')

        self.assertIsNone(
            extract_export_analysis_question("Export these results to Excel.", "Which sports are biggest?")
        )

    @patch("gemini_service.client.models.generate_content")
    def test_export_request_prompt_contains_only_the_messages_never_result_rows(self, generate_content):
        generate_content.return_value = SimpleNamespace(text='{"analysis_question": "  "}')

        self.assertIsNone(extract_export_analysis_question("Export the data.", "Which sports?"))
        prompt = generate_content.call_args.kwargs["contents"]
        self.assertIn("Export the data.", prompt)
        self.assertIn("Which sports?", prompt)


class RegionFilterTests(unittest.TestCase):
    REGIONS = ["منطقة الرياض", "المنطقة الشرقية", "منطقة مكة المكرمة"]
    TABLE = "`mos-gcp-host-dev-prj.dw_nsp_api_views.nsp_base_view_01`"

    def generate(self, sql, question, language="en"):
        with (
            patch("gemini_service.get_bigquery_schema", return_value="schema"),
            patch("gemini_service.get_filter_values", return_value={"region": self.REGIONS}),
            patch("gemini_service.client.models.generate_content", return_value=SimpleNamespace(text=sql)),
        ):
            return generate_sql(question, language, "nsp_base_view_01")

    def test_english_question_with_stored_arabic_value_passes(self):
        sql = f"SELECT COUNT(DISTINCT contactid) FROM {self.TABLE} WHERE region = 'منطقة الرياض'"
        self.assertEqual(self.generate(sql, "How many athletes are registered in Riyadh region?"), sql)

    def test_arabic_question_with_substring_pattern_passes(self):
        sql = f"SELECT COUNT(DISTINCT contactid) FROM {self.TABLE} WHERE region LIKE '%الرياض%'"
        self.assertEqual(self.generate(sql, "كم عدد الرياضيين في منطقة الرياض؟", "ar"), sql)

    def test_english_only_literal_is_rejected_with_available_regions(self):
        sql = f"SELECT COUNT(DISTINCT contactid) FROM {self.TABLE} WHERE LOWER(region) LIKE '%riyadh%'"
        result = self.generate(sql, "How many athletes are registered in Riyadh region?")

        self.assertTrue(result.startswith("CLARIFICATION:"))
        self.assertIn("منطقة الرياض", result)

    def test_unknown_region_returns_localized_clarification(self):
        sql = f"SELECT COUNT(*) FROM {self.TABLE} WHERE region = 'منطة غير موجودة'"
        arabic = self.generate(sql, "كم عدد الرياضيين؟", "ar")
        english = self.generate(sql, "How many athletes?")

        self.assertIn("القيم المتاحة", arabic)
        self.assertIn("Available values", english)

    def test_in_list_is_checked_and_queries_without_region_are_untouched(self):
        bad = f"SELECT COUNT(*) FROM {self.TABLE} WHERE region IN ('منطقة الرياض', 'Jeddah')"
        plain = f"SELECT COUNT(*) FROM {self.TABLE} WHERE registration_year = 2025"

        self.assertTrue(self.generate(bad, "q").startswith("CLARIFICATION:"))
        self.assertEqual(self.generate(plain, "q"), plain)

    def test_lookup_failure_does_not_block_generation(self):
        sql = f"SELECT COUNT(*) FROM {self.TABLE} WHERE LOWER(region) LIKE '%riyadh%'"
        with (
            patch("gemini_service.get_bigquery_schema", return_value="schema"),
            patch("gemini_service.get_filter_values", return_value={}),
            patch("gemini_service.client.models.generate_content", return_value=SimpleNamespace(text=sql)),
        ):
            self.assertEqual(generate_sql("q", "en", "nsp_base_view_01"), sql)

    def test_local_unavailable_filter_values_require_clarification(self):
        sql = f"SELECT COUNT(*) FROM {self.TABLE} WHERE region = 'منطقة الرياض'"
        with (
            patch("gemini_service.get_bigquery_schema", return_value="schema"),
            patch("gemini_service.get_filter_values", return_value=None),
            patch("gemini_service.client.models.generate_content", return_value=SimpleNamespace(text=sql)),
        ):
            result = generate_sql("How many athletes are registered in Riyadh region?")
        self.assertTrue(result.startswith("CLARIFICATION:"))
        self.assertIn("stored values for region are unavailable in LOCAL mode", result)

    def test_other_views_are_not_checked(self):
        sql = "SELECT COUNT(*) FROM `p.d.fr_base_view_01` WHERE region = 'anything'"
        with (
            patch("gemini_service.get_bigquery_schema", return_value="schema"),
            patch("gemini_service.get_filter_values") as values,
            patch("gemini_service.client.models.generate_content", return_value=SimpleNamespace(text=sql)),
        ):
            self.assertEqual(generate_sql("q", "en", "fr_base_view_01"), sql)
        values.assert_not_called()


if __name__ == "__main__":
    unittest.main()