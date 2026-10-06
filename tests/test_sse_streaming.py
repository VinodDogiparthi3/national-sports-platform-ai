import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app


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


if __name__ == "__main__":
    unittest.main()
