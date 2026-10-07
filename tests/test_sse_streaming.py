import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from config import BIGQUERY_DATASET_ID, BIGQUERY_PROJECT_ID, BIGQUERY_TABLE_ID


class StreamingAskTests(unittest.TestCase):
    question = "How many registrations are there for each sport?"
    sql = "SELECT sport, COUNT(*) AS total FROM `project.dataset.table` GROUP BY sport"

    def setUp(self):
        self.client = TestClient(app.app)

    def test_stream_endpoint_emits_status_sql_tokens_and_done(self):
        with (
            patch("app.generate_sql", return_value=self.sql),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql", return_value=(["sport", "total"], [("Football", 3)])),
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

    def test_arabic_ui_and_question_stream_arabic_response(self):
        with (
            patch("app.generate_sql", return_value=self.sql) as generate_sql,
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql", return_value=(["sport"], [("Football",)])),
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
        generate_sql.assert_called_once_with("ما الرياضة الأكثر تسجيلًا؟", "ar")
        answer_stream.assert_called_once_with(
            "ما الرياضة الأكثر تسجيلًا؟",
            ["sport"],
            [("Football",)],
            "ar",
        )

    def test_english_ui_and_question_stream_english_response(self):
        with (
            patch("app.generate_sql", return_value=self.sql),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql", return_value=(["sport"], [("Football",)])),
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
            patch("app.generate_sql", return_value=self.sql),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql", return_value=(["sport"], [("Football",)])),
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
            patch("app.generate_sql", return_value=self.sql),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql", return_value=(["sport", "total"], [("Football", 3)])),
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
        with patch("app.generate_sql", return_value="SELECT CURRENT_DATE()"):
            response = self.client.post("/ask", json={"question": "Tell me today's date"})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sql", response.json())
        self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)

    def test_stream_ask_clarifies_when_generated_query_has_no_table(self):
        with patch("app.generate_sql", return_value="SELECT CURRENT_DATE()"):
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
            with self.subTest(question=question), patch("app.generate_sql") as generate_sql:
                response = self.client.post("/ask", json={"question": question})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)
            self.assertNotIn("sql", response.json())
            generate_sql.assert_not_called()

    def test_stream_metadata_question_is_clarified_without_sql_generation(self):
        with patch("app.generate_sql") as generate_sql:
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
        with patch("app.generate_sql", return_value=query):
            response = self.client.post("/ask", json={"question": "Show company revenue"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)

    def test_json_ask_clarifies_when_generated_query_uses_unknown_column(self):
        query = (
            f"SELECT revenue FROM `{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}."
            f"{BIGQUERY_TABLE_ID}`"
        )
        with (
            patch("app.generate_sql", return_value=query),
            patch("sql_guardrails.get_allowed_columns", return_value={"sport"}),
        ):
            response = self.client.post("/ask", json={"question": "Show company revenue"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)

    def test_json_ask_hides_schema_validation_details(self):
        with (
            patch("app.generate_sql", return_value="SELECT * FROM generated_query"),
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
            patch("app.generate_sql", return_value="SELECT * FROM generated_query"),
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
