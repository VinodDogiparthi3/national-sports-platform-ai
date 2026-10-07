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
        self.assertIn('event: sql\ndata: {"sql":', response.text)
        self.assertIn('event: token\ndata: {"text": "## Counts\\n"}', response.text)
        self.assertIn('event: token\ndata: {"text": "\\nFootball: **3**"}', response.text)
        self.assertIn("event: done", response.text)

    def test_json_ask_contract_remains_unchanged(self):
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
            {"question": self.question, "sql": self.sql, "answer": "Legacy answer"},
        )

    def test_json_ask_clarifies_when_generated_query_has_no_table(self):
        with patch("app.generate_sql", return_value="SELECT CURRENT_DATE()"):
            response = self.client.post("/ask", json={"question": "Tell me today's date"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sql"], None)
        self.assertEqual(response.json()["answer"], app.OUT_OF_SCOPE_CLARIFICATION)

    def test_stream_ask_clarifies_when_generated_query_has_no_table(self):
        with patch("app.generate_sql", return_value="SELECT CURRENT_DATE()"):
            response = self.client.post(
                "/ask/stream",
                json={"question": "Tell me today's date"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(app.OUT_OF_SCOPE_CLARIFICATION, response.text)
        self.assertIn('event: done\ndata: {"question": "Tell me today\'s date", "sql": null}', response.text)
        self.assertNotIn("event: error", response.text)

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


if __name__ == "__main__":
    unittest.main()
