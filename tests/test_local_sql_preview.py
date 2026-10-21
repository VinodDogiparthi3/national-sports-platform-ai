import json
import socket
import unittest
from contextlib import contextmanager, ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from config import BIGQUERY_DATASET_ID, BIGQUERY_PROJECT_ID, BIGQUERY_TABLE_ID
from gemini_service import QueryPlan
from schema_provider import SchemaNotFoundError, UnauthorizedSchemaSourceError


def parse_events(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 2:
            events.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return events


class LocalSqlPreviewTests(unittest.TestCase):
    question = "Show registrations by sport"
    sql = f"SELECT sport FROM `{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}`"
    metadata = {"created_at": "2026-10-20T12:00:00Z", "identity": "sha256:fixture-manifest"}

    def setUp(self):
        self.client = TestClient(app.app)

    @contextmanager
    def request_patches(self, *, plan=None):
        with ExitStack() as stack:
            yield {
                "generate": stack.enter_context(patch("app.generate_query_plan", return_value=plan or QueryPlan(queries=((BIGQUERY_TABLE_ID, self.sql),)))),
                "execute": stack.enter_context(patch("app.execute_sql_batch")),
                "dry_run": stack.enter_context(patch("app.get_export_row_counts")),
                "results": stack.enter_context(patch("app.stream_query_rows_batch")),
                "bigquery_client": stack.enter_context(patch("google.cloud.bigquery.Client")),
                "connect": stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("network access forbidden"))),
                "dns": stack.enter_context(patch("socket.getaddrinfo", side_effect=AssertionError("DNS access forbidden"))),
                "provider_mode": stack.enter_context(patch("app.SCHEMA_PROVIDER", "LOCAL")),
                "catalog_mode": stack.enter_context(patch("view_catalog.SCHEMA_PROVIDER", "LOCAL")),
                "metadata_mode": stack.enter_context(patch("schema_metadata.SCHEMA_PROVIDER", "LOCAL")),
                "allowed_columns": stack.enter_context(patch("sql_guardrails.get_allowed_columns", return_value={"sport"})),
                "backup_metadata": stack.enter_context(patch("schema_metadata.get_local_schema_backup_metadata", return_value=self.metadata)),
                "answer": stack.enter_context(patch("app.generate_answer")),
                "answer_stream": stack.enter_context(patch("app.generate_answer_stream")),
            }

    def test_valid_preview_is_distinct_and_never_executes(self):
        with self.request_patches() as patches:
            response = self.client.post("/ask/stream", json={"question": self.question})

        self.assertEqual(response.status_code, 200)
        events = parse_events(response.text)
        self.assertEqual([event for event, _ in events].count("sql_preview"), 1)
        preview = next(payload for event, payload in events if event == "sql_preview")
        self.assertEqual(preview["status"], "PREVIEW_ONLY")
        self.assertEqual(preview["notice"], "No query executed; no results available.")
        self.assertEqual(preview["authorized_source_ids"], [BIGQUERY_TABLE_ID])
        self.assertEqual(preview["schema_backup"], self.metadata)
        self.assertEqual(preview["queries"][0]["source_id"], BIGQUERY_TABLE_ID)
        self.assertIn("LIMIT 100", preview["queries"][0]["sql"])
        self.assertEqual(events[-1][0], "done")
        self.assertFalse(any(event in {"token", "source", "view_result", "context", "export"} for event, _ in events))
        for key in ("execute", "dry_run", "results", "bigquery_client"):
            patches[key].assert_not_called()
        patches["answer"].assert_not_called()
        patches["answer_stream"].assert_not_called()
        patches["connect"].assert_not_called()
        patches["dns"].assert_not_called()

    def test_json_ask_returns_only_the_preview_contract(self):
        with self.request_patches():
            response = self.client.post("/ask", json={"question": self.question})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"question", "sql_preview"})
        self.assertNotIn("answer", response.json())
        self.assertEqual(response.json()["sql_preview"]["status"], "PREVIEW_ONLY")

    def test_invalid_sql_is_rejected_without_a_preview_or_execution(self):
        invalid_plan = QueryPlan(queries=((BIGQUERY_TABLE_ID, "DELETE FROM `project.dataset.table`"),))
        with self.request_patches(plan=invalid_plan) as patches:
            response = self.client.post("/ask/stream", json={"question": self.question})

        events = parse_events(response.text)
        self.assertFalse(any(event == "sql_preview" for event, _ in events))
        self.assertIn(app.SQL_VALIDATION_MESSAGES["en"], "".join(payload.get("text", "") for _, payload in events))
        patches["execute"].assert_not_called()

    def test_unauthorized_sql_source_is_rejected_by_existing_guardrails(self):
        unauthorized_plan = QueryPlan(queries=((BIGQUERY_TABLE_ID, "SELECT sport FROM `other-project.other-dataset.other-table`"),))
        with self.request_patches(plan=unauthorized_plan) as patches:
            response = self.client.post("/ask/stream", json={"question": self.question})

        events = parse_events(response.text)
        self.assertFalse(any(event == "sql_preview" for event, _ in events))
        self.assertIn(app.SQL_VALIDATION_MESSAGES["en"], "".join(payload.get("text", "") for _, payload in events))
        patches["execute"].assert_not_called()

    def test_missing_schema_is_clarified_for_json_and_sse(self):
        for endpoint in ("/ask", "/ask/stream"):
            with self.subTest(endpoint=endpoint), self.request_patches() as patches:
                patches["generate"].side_effect = SchemaNotFoundError("private missing source detail")
                response = self.client.post(endpoint, json={"question": self.question})

            self.assertEqual(response.status_code, 200)
            body = response.json() if endpoint == "/ask" else {"answer": response.text}
            self.assertIn("no approved schema", body.get("answer", response.text))
            self.assertNotIn("private missing source detail", response.text)
            self.assertNotIn("sql_preview", response.text)
            patches["execute"].assert_not_called()

    def test_missing_filter_values_use_existing_clarification_without_preview(self):
        clarification = "CLARIFICATION: Approved stored values for region are unavailable in LOCAL mode."
        plan = QueryPlan(clarification=clarification)
        with self.request_patches(plan=plan) as patches:
            response = self.client.post("/ask/stream", json={"question": "Show registrations for an exact region"})

        events = parse_events(response.text)
        self.assertIn(clarification, "".join(payload.get("text", "") for _, payload in events))
        self.assertFalse(any(event == "sql_preview" for event, _ in events))
        patches["execute"].assert_not_called()

    def test_export_and_download_are_denied_without_data_access(self):
        with (
            patch("app.SCHEMA_PROVIDER", "LOCAL"),
            patch("app.generate_query_plan") as plan,
            patch("app.decrypt_token") as decrypt,
            patch("app.get_export_row_counts") as dry_run,
            patch("app.stream_query_rows_batch") as stream_results,
        ):
            export = self.client.post("/ask/stream", json={"question": "Export these results to Excel"})
            download = self.client.get("/exports/not-a-token")

        self.assertEqual(download.status_code, 403)
        self.assertIn("only provides validated SQL previews", export.text)
        self.assertNotIn("event: export", export.text)
        plan.assert_not_called()
        decrypt.assert_not_called()
        dry_run.assert_not_called()
        stream_results.assert_not_called()

    def test_follow_ups_cannot_use_preview_as_real_results(self):
        history = [{"role": "assistant", "text": "PREVIEW_ONLY", "is_result": True}]
        for endpoint, question in (("/ask", "Explain the previous results"), ("/ask/stream", "Translate the previous answer")):
            with (
                self.subTest(endpoint=endpoint, question=question),
                patch("app.SCHEMA_PROVIDER", "LOCAL"),
                patch("app.generate_query_plan") as plan,
                patch("app.explain_previous_results") as explain,
                patch("app.translate_result_preserving_numbers") as translate,
                patch("app.execute_sql_batch") as execute,
            ):
                response = self.client.post(endpoint, json={"question": question, "conversation_history": history})

            payload = response.json() if endpoint == "/ask" else {"answer": response.text}
            self.assertIn("only provides validated SQL previews", payload.get("answer", response.text))
            plan.assert_not_called()
            explain.assert_not_called()
            translate.assert_not_called()
            execute.assert_not_called()

    def test_local_planning_does_not_receive_saved_results_or_completed_context(self):
        prior_history = [{"role": "assistant", "text": "old client results", "is_result": True}]
        completed_context = {"pending": None, "last_question": "Old question", "view_ids": [BIGQUERY_TABLE_ID]}
        with self.request_patches() as patches:
            response = self.client.post("/ask/stream", json={
                "question": self.question,
                "conversation_history": prior_history,
                "conversation_context": completed_context,
            })

        self.assertEqual(response.status_code, 200)
        patches["generate"].assert_called_once_with(self.question, "en", (), None)

    def test_health_exposes_provider_without_cloud_identifiers(self):
        with patch("app.SCHEMA_PROVIDER", "LOCAL"):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "healthy", "schema_provider": "LOCAL"})
        self.assertNotIn(BIGQUERY_PROJECT_ID, response.text)

    def test_existing_unauthorized_source_exception_is_a_safe_clarification(self):
        with self.request_patches() as patches:
            patches["generate"].side_effect = UnauthorizedSchemaSourceError("private unauthorized source")
            response = self.client.post("/ask/stream", json={"question": self.question})

        self.assertIn("no approved schema", response.text)
        self.assertNotIn("private unauthorized source", response.text)
        self.assertNotIn("sql_preview", response.text)


if __name__ == "__main__":
    unittest.main()