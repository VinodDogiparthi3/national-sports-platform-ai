import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from config import BIGQUERY_DATASET_ID, BIGQUERY_PROJECT_ID, BIGQUERY_TABLE_ID
from gemini_service import QueryPlan


class StreamingAskTests(unittest.TestCase):
    question = "How many registrations are there for each sport?"
    sql = "SELECT sport, COUNT(*) AS total FROM `project.dataset.table` GROUP BY sport"

    def setUp(self):
        self.client = TestClient(app.app)

    def plan(self, sql):
        return QueryPlan(queries=(("nsp_base_view_01", sql),))

    def test_stream_endpoint_emits_status_sql_tokens_and_done(self):
        with (
            patch("app.generate_query_plan", return_value=self.plan(self.sql)),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql_batch", return_value=[(["sport", "total"], [("Football", 3)])]),
            patch("app.generate_answer_stream", return_value=iter(["## Counts\n", "\nFootball: **3**"])),
        ):
            response = self.client.post("/ask/stream", json={"question": self.question})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn('event: status\ndata: {"step": "understanding"}', response.text)
        self.assertNotIn("event: sql", response.text)
        self.assertIn('event: token\ndata: {"text": "## Counts\\n"}', response.text)
        self.assertIn('event: token\ndata: {"text": "\\nFootball: **3**"}', response.text)
        self.assertIn('event: done\ndata: {"question":', response.text)

    def test_both_selection_queries_each_pending_view_and_completes_sse(self):
        context = {
            "pending": {
                "kind": "views",
                "question": "How many athletes are registered?",
                "options": [
                    {"view_id": "nsp_base_view_01"},
                    {"view_id": "sopc_view_01"},
                ],
            },
            "last_question": "How many athletes are registered?",
            "view_ids": ["nsp_base_view_01", "sopc_view_01"],
        }
        with (
            patch("view_router.route_question") as route,
            patch("gemini_service.generate_sql", side_effect=["SELECT 1", "SELECT 2"]) as generate_sql,
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql_batch", return_value=[(["count"], [(12,)]), (["count"], [(7,)])]) as execute,
            patch("app.generate_answer_stream", side_effect=[iter(["Athletes: 12"]), iter(["Athletes: 7"])]) as answers,
        ):
            response = self.client.post(
                "/ask/stream",
                json={"question": "both", "language": "en", "conversation_context": context},
            )

        route.assert_not_called()
        self.assertEqual(generate_sql.call_count, 2)
        self.assertEqual(execute.call_args.args[0], ["SELECT 1", "SELECT 2"])
        self.assertEqual(answers.call_count, 2)
        self.assertIn("### NSP Base View", response.text)
        self.assertIn("### Sports Competitions and Athlete Performance View", response.text)
        self.assertIn('event: done\ndata: {"question": "both"}', response.text)
        self.assertIn('"conversation_context": {"pending": null', response.text)

    def test_year_selection_uses_pending_original_question_and_selected_view(self):
        context = {
            "pending": {
                "kind": "years",
                "question": "Show population by age group",
                "options": [
                    {"view_id": "vw_health_determinants_statistics_publication", "year": 2023},
                    {"view_id": "population_estimates_statistics_view_01", "year": 2024},
                ],
            }
        }
        with (
            patch("view_router.route_question") as route,
            patch("gemini_service.generate_sql", return_value="SELECT 1") as generate_sql,
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql_batch", return_value=[(["age_group"], [("Adult",)])]),
            patch("app.generate_answer_stream", return_value=iter(["Adult population"])),
        ):
            response = self.client.post(
                "/ask/stream",
                json={"question": "2024", "language": "en", "conversation_context": context},
            )

        route.assert_not_called()
        generate_sql.assert_called_once_with(
            "Show population by age group for 2024",
            "en",
            "population_estimates_statistics_view_01",
            [],
        )
        self.assertIn("Adult population", response.text)
        self.assertIn('event: done\ndata: {"question": "2024"}', response.text)

    def test_translation_follow_up_uses_previous_result_without_querying(self):
        history = [
            {"role": "assistant", "text": "Total athletes: 13,650", "is_result": True}
        ]
        with (
            patch("app.translate_result_preserving_numbers", return_value="إجمالي الرياضيين: 13,650") as translate,
            patch("app.generate_query_plan") as plan,
            patch("app.execute_sql_batch") as execute,
        ):
            response = self.client.post(
                "/ask/stream",
                json={
                    "question": "translate the above results to English",
                    "language": "ar",
                    "conversation_history": history,
                },
            )

        translate.assert_called_once_with("Total athletes: 13,650", "en")
        plan.assert_not_called()
        execute.assert_not_called()
        self.assertIn("إجمالي الرياضيين: 13,650", response.text)
        self.assertIn('event: done\ndata: {"question": "translate the above results to English"}', response.text)

    def test_arabic_ui_and_question_stream_arabic_response(self):
        with (
            patch("app.generate_query_plan", return_value=self.plan(self.sql)) as generate_plan,
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql_batch", return_value=[(["sport"], [("Football",)])]),
            patch(
                "app.generate_answer_stream",
                return_value=iter(["الرياضة الأكثر تسجيلًا هي Football."]),
            ) as answer_stream,
        ):
            response = self.client.post(
                "/ask/stream",
                json={"question": "ما الرياضة الأكثر تسجيلًا؟", "language": "ar"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("الرياضة الأكثر تسجيلًا هي Football.", response.text)
        generate_plan.assert_called_once()
        answer_stream.assert_called_once_with(
            "ما الرياضة الأكثر تسجيلًا؟",
            ["sport"],
            [("Football",)],
            "ar",
        )

    def test_english_ui_and_question_stream_english_response(self):
        with (
            patch("app.generate_query_plan", return_value=self.plan(self.sql)),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql_batch", return_value=[(["sport"], [("Football",)])]),
            patch(
                "app.generate_answer_stream",
                return_value=iter(["Football has the most registrations."]),
            ) as answer_stream,
        ):
            response = self.client.post(
                "/ask/stream",
                json={
                    "question": "Which sport has the most registrations?",
                    "language": "en",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Football has the most registrations.", response.text)
        answer_stream.assert_called_once_with(
            "Which sport has the most registrations?",
            ["sport"],
            [("Football",)],
            "en",
        )

    def test_arabic_question_keeps_arabic_narrative_with_english_database_value(self):
        with (
            patch("app.generate_query_plan", return_value=self.plan(self.sql)),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql_batch", return_value=[(["sport"], [("Football",)])]),
            patch(
                "app.generate_answer_stream",
                return_value=iter(["الرياضة الأكثر تسجيلًا هي Football."]),
            ) as answer_stream,
        ):
            response = self.client.post(
                "/ask/stream",
                json={"question": "ما الرياضة الأكثر تسجيلًا؟", "language": "en"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("الرياضة الأكثر تسجيلًا هي Football.", response.text)
        answer_stream.assert_called_once_with(
            "ما الرياضة الأكثر تسجيلًا؟",
            ["sport"],
            [("Football",)],
            "ar",
        )

    def test_json_ask_returns_answer_without_generated_sql(self):
        with (
            patch("app.generate_query_plan", return_value=self.plan(self.sql)),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql_batch", return_value=[(["sport", "total"], [("Football", 3)])]),
            patch("app.generate_answer", return_value="Legacy answer"),
        ):
            response = self.client.post("/ask", json={"question": self.question})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"question": self.question, "answer": "Legacy answer"},
        )

    def test_version_endpoint_reports_api_and_cloud_run_revision(self):
        with (
            patch.object(app.app, "version", "2.0.0"),
            patch.dict(
                os.environ,
                {
                    "K_SERVICE": "national-sports-platform-ai-v2",
                    "K_REVISION": "national-sports-platform-ai-v2-00002-j45",
                },
            ),
        ):
            response = self.client.get("/version")

        self.assertEqual(
            response.json(),
            {
                "version": "2.0.0",
                "service": "national-sports-platform-ai-v2",
                "revision": "national-sports-platform-ai-v2-00002-j45",
            },
        )

    def test_question_translation_endpoint_returns_display_translations(self):
        questions = ["How many registrations are there for Football?"]
        translations = ["كم عدد التسجيلات لرياضة Football؟"]
        with patch("app.translate_questions_to_arabic", return_value=translations) as translate:
            response = self.client.post(
                "/translate/questions",
                json={"questions": questions},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"translations": translations})
        translate.assert_called_once_with(questions)

    def test_conversation_translation_endpoint_accepts_both_languages(self):
        texts = ["Total registrations are 132,021."]
        translation = ["إجمالي التسجيلات هو 132,021."]
        with patch("app.translate_texts", return_value=translation) as translate:
            response = self.client.post(
                "/translate/texts",
                json={"texts": texts, "target_language": "ar"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"translations": translation})
        translate.assert_called_once_with(texts, "ar")

    def test_json_ask_clarifies_when_generated_query_has_no_table(self):
        plan = QueryPlan(clarification=app.OUT_OF_SCOPE_CLARIFICATION)
        with patch("app.generate_query_plan", return_value=plan):
            response = self.client.post("/ask", json={"question": "Tell me today's date"})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sql", response.json())
        self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)

    def test_stream_ask_clarifies_when_generated_query_has_no_table(self):
        plan = QueryPlan(clarification=app.OUT_OF_SCOPE_CLARIFICATION)
        with patch("app.generate_query_plan", return_value=plan):
            response = self.client.post(
                "/ask/stream",
                json={"question": "Tell me today's date"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(app.OUT_OF_SCOPE_CLARIFICATION, response.text)
        self.assertIn('event: done\ndata: {"question": "Tell me today\'s date"}', response.text)
        self.assertNotIn("event: error", response.text)

    def test_metadata_questions_are_clarified_without_sql_generation(self):
        questions = (
            "How many rows are there?",
            "Show row count",
            "What columns are available?",
            "What is the schema?",
            "How many columns are there?",
        )

        for question in questions:
            with self.subTest(question=question), patch("app.generate_query_plan") as generate_sql:
                response = self.client.post("/ask", json={"question": question})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)
            self.assertNotIn("sql", response.json())
            generate_sql.assert_not_called()

    def test_stream_metadata_question_is_clarified_without_sql_generation(self):
        with patch("app.generate_query_plan") as generate_sql:
            response = self.client.post(
                "/ask/stream",
                json={"question": "What columns are available?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(app.OUT_OF_SCOPE_CLARIFICATION, response.text)
        self.assertNotIn("event: sql", response.text)
        self.assertNotIn("event: error", response.text)
        generate_sql.assert_not_called()

    def test_json_ask_clarifies_when_generated_query_uses_disallowed_table(self):
        query = "SELECT * FROM `other-project.other-dataset.other-table`"
        plan = QueryPlan(queries=(("nsp_base_view_01", query),))
        with patch("app.generate_query_plan", return_value=plan):
            response = self.client.post("/ask", json={"question": "Show company revenue"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)

    def test_json_ask_clarifies_when_generated_query_uses_unknown_column(self):
        query = (
            f"SELECT revenue FROM `{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}."
            f"{BIGQUERY_TABLE_ID}`"
        )
        with (
            patch("app.generate_query_plan", return_value=QueryPlan(queries=(("nsp_base_view_01", query),))),
            patch("sql_guardrails.get_allowed_columns", return_value={"sport"}),
        ):
            response = self.client.post("/ask", json={"question": "Show company revenue"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)

    def test_json_ask_hides_schema_validation_details(self):
        with (
            patch("app.generate_query_plan", return_value=QueryPlan(queries=(("nsp_base_view_01", "SELECT * FROM generated_query"),))),
            patch(
                "app.validate_sql",
                side_effect=ValueError("Table is not allowed: information_schema.columns"),
            ),
        ):
            response = self.client.post("/ask", json={"question": "Show registrations"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)
        self.assertNotIn("information_schema", response.text)

    def test_stream_ask_hides_schema_validation_details(self):
        with (
            patch("app.generate_query_plan", return_value=QueryPlan(queries=(("nsp_base_view_01", "SELECT * FROM generated_query"),))),
            patch(
                "app.validate_sql",
                side_effect=ValueError("Table is not allowed: information_schema.columns"),
            ),
        ):
            response = self.client.post(
                "/ask/stream",
                json={"question": "Show registrations"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(app.OUT_OF_SCOPE_CLARIFICATION, response.text)
        self.assertNotIn("information_schema", response.text)
        self.assertNotIn("event: error", response.text)


if __name__ == "__main__":
    unittest.main()
