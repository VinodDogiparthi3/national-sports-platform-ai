import os
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from config import BIGQUERY_DATASET_ID, BIGQUERY_PROJECT_ID, BIGQUERY_TABLE_ID
from bigquery_catalog import ColumnMetadata, DiscoveredSource
from bigquery_database import QueryBudgetExceeded
from gemini_service import QueryPlan
from view_catalog import ApprovedTable, get_view
from view_router import RouteResult


def parse_events(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 2:
            events.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return events


class StreamingAskTests(unittest.TestCase):
    question = "How many registrations are there for each sport?"
    sql = "SELECT sport, COUNT(*) AS total FROM `project.dataset.table` GROUP BY sport"

    def setUp(self):
        self.client = TestClient(app.app)

    def plan(self, sql):
        return QueryPlan(queries=(("nsp_base_view_01", sql),))

    def test_missing_entity_clarification_round_trips_through_both_apis(self):
        from view_router import RouteResult

        question = "List individuals affiliated with a specific club or federation"
        clarification = "Which club or federation should I use?"
        for endpoint in ("/ask", "/ask/stream"):
            with (
                self.subTest(endpoint=endpoint),
                patch("view_router.route_question", return_value=RouteResult(source_id="nsp_base_view_01")) as route,
                patch("gemini_service.generate_sql", side_effect=[
                    f"CLARIFICATION: {clarification}",
                    "CLARIFICATION: Please provide the club and federation names.",
                ]) as generate,
                patch("app.is_metadata_question", return_value=False),
                patch("app.execute_sql_batch") as execute,
            ):
                initial = self.client.post(endpoint, json={"question": question})
                self.assertEqual(initial.status_code, 200)
                if endpoint == "/ask":
                    context = initial.json()["conversation_context"]
                else:
                    context = next(payload["conversation_context"] for event, payload in parse_events(initial.text) if event == "context")
                self.assertEqual(context["pending"]["kind"], "details")
                follow_up = self.client.post(endpoint, json={"question": "both", "conversation_context": context})
                self.assertEqual(follow_up.status_code, 200)
                self.assertIn(question, generate.call_args.args[0])
                self.assertIn(clarification, generate.call_args.args[0])
                self.assertIn("User reply: both", generate.call_args.args[0])
                route.assert_called_once()
                execute.assert_not_called()

    def test_legacy_population_region_selection_reports_limitation_without_model_or_execution(self):
        question = "total no.of regions and list of regions"
        source_ids = ["population_estimates_statistics_view_01", "nsp_base_view_01"]
        context = {"pending": {"kind": "views", "question": question,
                               "options": [{"view_id": source_id} for source_id in source_ids]},
                   "last_question": question, "view_ids": source_ids}
        for endpoint, language in (("/ask", "en"), ("/ask/stream", "en"), ("/ask", "ar"), ("/ask/stream", "ar")):
            with (
                self.subTest(endpoint=endpoint, language=language),
                patch("view_router.route_question") as route,
                patch("gemini_service.client.models.generate_content") as model,
                patch("gemini_service.get_bigquery_schema") as schema,
                patch("app.is_metadata_question") as classify,
                patch("app.execute_sql_batch") as execute,
            ):
                response = self.client.post(endpoint, json={"question": "all", "language": language, "conversation_context": context})
            self.assertEqual(response.status_code, 200)
            route.assert_not_called()
            model.assert_not_called()
            schema.assert_not_called()
            classify.assert_not_called()
            execute.assert_not_called()
            if endpoint == "/ask":
                message = response.json()["answer"]
                retained = response.json()["conversation_context"]
            else:
                events = parse_events(response.text)
                message = "".join(payload["text"] for event, payload in events if event == "token")
                retained = next(payload["conversation_context"] for event, payload in events if event == "context")
                self.assertEqual(events[-1][0], "done")
            self.assertIn("Population Estimates Statistics View", message)
            self.assertIn("Regional breakdowns are not supported" if language == "en" else "لا يدعم عدد المناطق", message)
            self.assertEqual(retained["view_ids"], source_ids)
            self.assertEqual(retained["pending"]["kind"], "details")
            self.assertEqual(retained["pending"]["question"], question)
            self.assertEqual(retained["pending"]["options"], context["pending"]["options"])
            self.assertIn("Regional breakdowns are not supported" if language == "en" else "لا يدعم عدد المناطق", retained["pending"]["clarification"])

    def test_one_source_clarification_blocks_entire_batch_before_validation_in_both_apis(self):
        question = "total no.of regions and list of regions"
        source_ids = ("dw_ms_crm_onprem.ntw_regionBase", "population_estimates_statistics_view_01", "nsp_base_view_01")
        crm = ApprovedTable("dw_ms_crm_onprem", "ntw_regionBase", "ntw_regionBase", "Regions", "CRM regions")
        context = {"pending": {"kind": "views", "question": question,
                               "options": [{"view_id": source_id} for source_id in source_ids]},
                   "last_question": question, "view_ids": list(source_ids)}
        clarification = "Please clarify the region definition."
        for endpoint, language in (("/ask", "en"), ("/ask/stream", "en"), ("/ask", "ar"), ("/ask/stream", "ar")):
            for reply in ("all", "provide for all"):
                for blocked_index in range(len(source_ids)):
                    answers = ["SELECT 1", "SELECT 2", "SELECT 3"]
                    answers[blocked_index] = f"CLARIFICATION: {clarification}"
                    with (
                        self.subTest(endpoint=endpoint, language=language, reply=reply, blocked_index=blocked_index),
                        patch("view_catalog.get_authorized_tables", return_value=(crm,)),
                        patch("view_router.route_question") as route,
                        patch("gemini_service.generate_sql", side_effect=answers) as generate,
                        patch("app.is_metadata_question") as classify,
                        patch("app.validate_sql") as validate,
                        patch("app.execute_sql_batch") as execute,
                        self.assertLogs("uvicorn.error", level="INFO") as captured,
                    ):
                        response = self.client.post(endpoint, json={"question": reply, "language": language, "conversation_context": context})
                    self.assertEqual(response.status_code, 200)
                    route.assert_not_called()
                    classify.assert_not_called()
                    validate.assert_not_called()
                    execute.assert_not_called()
                    self.assertEqual(generate.call_count, blocked_index + 1)
                    if endpoint == "/ask":
                        message = response.json()["answer"]
                        retained = response.json()["conversation_context"]
                        self.assertNotIn("sources", response.json())
                    else:
                        events = parse_events(response.text)
                        message = "".join(payload["text"] for event, payload in events if event == "token")
                        self.assertFalse(any(event in {"source", "view_result", "error"} for event, _ in events))
                        retained = next(payload["conversation_context"] for event, payload in events if event == "context")
                        self.assertEqual(events[-1][0], "done")
                    source = crm if blocked_index == 0 else get_view(source_ids[blocked_index])
                    self.assertIn(source.display_name, message)
                    self.assertIn(clarification, message)
                    self.assertIn("يلزم توضيح" if language == "ar" else "Clarification needed", message)
                    self.assertEqual(retained, {
                        **context,
                        "pending": {**context["pending"], "kind": "details", "clarification": clarification},
                    })
                    stages = [json.loads(record.getMessage()) for record in captured.records]
                    self.assertEqual([entry["stage"] for entry in stages], [
                        "received", "selection_checked", "planning_started", "clarification_sent",
                    ])
                    self.assertTrue(stages[1]["selection_resolved"])
                    self.assertEqual(stages[1]["selected_source_count"], 3)
                    self.assertEqual(stages[-1]["pending_option_count"], 3)
                    self.assertEqual(stages[-1]["source_position"], blocked_index + 1)
                    self.assertEqual(stages[-1]["source_status"], "needs_clarification")
                    self.assertEqual(stages[-1]["reason"], "source_planning_clarification")
                    logged = "\n".join(captured.output)
                    for private_value in (question, clarification, source.display_name, *source_ids):
                        self.assertNotIn(private_value, logged)

    def test_validation_rejection_preserves_context_and_logs_only_safe_metadata(self):
        private_question = "PRIVATE_QUESTION_MARKER"
        private_sql = "SELECT PRIVATE_SQL_MARKER FROM secret_source"
        private_error = "PRIVATE_ERROR_MARKER bearer-token row-value"
        context = {
            "pending": {"kind": "views", "question": private_question, "options": [{"view_id": "nsp_base_view_01"}]},
            "last_question": private_question, "view_ids": ["nsp_base_view_01"],
        }
        plan = QueryPlan(queries=(("nsp_base_view_01", private_sql),), conversation_context={
            "pending": None, "last_question": private_question, "view_ids": ["nsp_base_view_01"],
        })
        for endpoint in ("/ask", "/ask/stream"):
            for language in ("en", "ar"):
                with (
                    self.subTest(endpoint=endpoint, language=language),
                    patch("app.generate_query_plan", return_value=plan),
                    patch("app.validate_sql", side_effect=ValueError(private_error)),
                    patch("app.execute_sql_batch") as execute,
                    self.assertLogs("uvicorn.error", level="INFO") as captured,
                ):
                    response = self.client.post(endpoint, json={
                        "question": "all", "language": language, "conversation_context": context,
                    })
                self.assertEqual(response.status_code, 200)
                execute.assert_not_called()
                if endpoint == "/ask":
                    retained = response.json()["conversation_context"]
                    message = response.json()["answer"]
                else:
                    events = parse_events(response.text)
                    retained = next(payload["conversation_context"] for event, payload in events if event == "context")
                    message = "".join(payload["text"] for event, payload in events if event == "token")
                    self.assertEqual(events[-1][0], "done")
                self.assertEqual(message, app.SQL_VALIDATION_MESSAGES[language])
                self.assertEqual(retained, app.ConversationContext(**context).model_dump())
                records = [json.loads(record.getMessage()) for record in captured.records]
                self.assertEqual([record["stage"] for record in records], [
                    "received", "selection_checked", "planning_started", "validation_rejected",
                ])
                self.assertEqual(len({record["request_id"] for record in records}), 1)
                self.assertTrue(records[1]["selection_resolved"])
                self.assertEqual(records[-1]["pending_option_count"], 1)
                self.assertEqual(records[-1]["selected_source_count"], 1)
                self.assertEqual(records[-1]["query_count"], 1)
                self.assertEqual(records[-1]["reason"], "sql_guardrail_rejection")
                allowed_keys = {"event", "request_id", "stage", "has_context", "pending_option_count",
                                "selection_resolved", "selected_source_count", "query_count", "reason"}
                for record in records:
                    self.assertEqual(set(record), allowed_keys)
                logged = "\n".join(captured.output)
                for private_value in (private_question, private_sql, private_error, "nsp_base_view_01"):
                    self.assertNotIn(private_value, logged)

    def test_legacy_cloud_region_clarification_keeps_context_even_when_sql_is_rejected(self):
        question = "total no.of regions and list of regions"
        cloud_text = "Which source should I use?\n(ntw_regionBase; Population Estimates Statistics View;\nNSP Base View)"
        source_ids = ("dw_ms_crm_onprem.ntw_regionBase", "population_estimates_statistics_view_01", "nsp_base_view_01")
        crm = ApprovedTable("dw_ms_crm_onprem", "ntw_regionBase", "ntw_regionBase", "Regions", "CRM regions")
        sources = (crm, get_view(source_ids[1]), get_view(source_ids[2]))
        for reply in ("all", "provide for all"):
            for reject_sql in (False, True):
                with (
                    self.subTest(reply=reply, reject_sql=reject_sql),
                    patch("view_catalog.get_authorized_tables", return_value=(crm,)),
                    patch("view_router.route_question", return_value=RouteResult(
                        candidate_view_ids=source_ids, clarification=cloud_text,
                    )) as route,
                    patch("gemini_service.generate_sql", side_effect=lambda original, language, source_id, history:
                          f"SELECT `{'region' if reject_sql or source_id != crm.source_id else 'ntw_name'}` FROM `{next(source for source in sources if source.source_id == source_id).full_id}`") as sql,
                    patch("sql_guardrails.get_view_columns", side_effect=lambda source_id:
                          [("ntw_name" if source_id == crm.source_id else "region", "STRING", "NULLABLE")]),
                    patch("app.is_metadata_question", return_value=False) as classify,
                    patch("app.execute_sql_batch", return_value=[(["region"], [("Mock region",)])] * 3) as execute,
                    patch("app.generate_answer_stream", side_effect=lambda *args: iter(["Mock region"])),
                ):
                    initial = self.client.post("/ask/stream", json={"question": question})
                    initial_events = parse_events(initial.text)
                    context = next(payload["conversation_context"] for event, payload in initial_events if event == "context")
                    message = "".join(payload["text"] for event, payload in initial_events if event == "token")
                    self.assertEqual(" ".join(message.split()), " ".join(cloud_text.split()))
                    self.assertEqual([option["view_id"] for option in context["pending"]["options"]], list(source_ids))
                    self.assertEqual(context["pending"]["question"], question)
                    self.assertLess([event for event, _ in initial_events].index("context"), [event for event, _ in initial_events].index("token"))
                    request = app.QuestionRequest(question=reply, conversation_context=context)
                    self.assertEqual(app.resolve_pending_selection(request.question, request.conversation_context)["view_ids"], list(source_ids))
                    response = self.client.post("/ask/stream", json=request.model_dump())
                    events = parse_events(response.text)
                    route.assert_called_once()
                    classify.assert_called_once()
                    self.assertEqual([call.args[0] for call in sql.call_args_list], [question] * 3)
                    self.assertEqual(events[-1][0], "done")
                    if reject_sql:
                        execute.assert_not_called()
                        self.assertNotIn(app.OUT_OF_SCOPE_CLARIFICATION, response.text)
                        self.assertIn("generated SQL", response.text)
                        retained = next(payload["conversation_context"] for event, payload in events if event == "context")
                        self.assertEqual(retained, request.conversation_context.model_dump())
                    else:
                        execute.assert_called_once()
                        self.assertEqual([payload["view_id"] for event, payload in events if event == "view_result"], list(source_ids))

    def test_missing_gemini_candidates_are_repaired_for_json_and_sse_follow_ups(self):
        question = "Compare reporting totals across the selected sources"
        source_ids = ("dw_ms_crm_onprem.ntw_regionBase", "nsp_base_view_01", "population_estimates_statistics_view_01")
        crm = ApprovedTable("dw_ms_crm_onprem", "ntw_regionBase", "CRM regions", "Regions", "CRM region list")
        sources = (crm, get_view(source_ids[1]), get_view(source_ids[2]))
        sql_by_source = {source.source_id: f"SELECT `region` FROM `{source.full_id}`" for source in sources}
        for endpoint in ("/ask", "/ask/stream"):
            for reply, selected in (("all", source_ids), ("both", source_ids), ("provide for all", source_ids),
                                    ("1st", source_ids[:1]), ("2nd", source_ids[1:2]), ("3rd", source_ids[2:])):
                with (
                    self.subTest(endpoint=endpoint, reply=reply),
                    patch("view_router.get_queryable_sources", return_value=sources),
                    patch("view_catalog.get_authorized_tables", return_value=(crm,)),
                    patch("gemini_service.client.models.generate_content", side_effect=[
                        SimpleNamespace(text=json.dumps({"clarification": "1. CRM 2. NSP 3. Population"})),
                        SimpleNamespace(text=json.dumps({"source_id": None, "candidates": list(source_ids)})),
                    ]) as model,
                    patch("gemini_service.generate_sql", side_effect=lambda original, language, source_id, history: sql_by_source[source_id]) as generate_sql,
                    patch("sql_guardrails.get_view_columns", return_value=[("region", "STRING", "NULLABLE")]),
                    patch("app.execute_sql_batch", side_effect=lambda queries: [(["region"], [("Mock region",)]) for _ in queries]) as execute,
                    patch("app.generate_answer_stream", side_effect=lambda *args: iter(["Mock region"])),
                    patch("app.generate_answer", return_value="Mock region"),
                ):
                    initial = self.client.post(endpoint, json={"question": question})
                    self.assertEqual(initial.status_code, 200)
                    context = initial.json()["conversation_context"] if endpoint == "/ask" else next(
                        payload["conversation_context"] for event, payload in parse_events(initial.text) if event == "context"
                    )
                    self.assertEqual(context["pending"]["question"], question)
                    self.assertEqual([option["view_id"] for option in context["pending"]["options"]], list(source_ids))
                    execute.assert_not_called()
                    response = self.client.post(endpoint, json={"question": reply, "conversation_context": context})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(model.call_count, 2)
                    self.assertEqual([call.args[0] for call in generate_sql.call_args_list], [question] * len(selected))
                    execute.assert_called_once_with([sql_by_source[source_id] + " LIMIT 100" for source_id in selected])
                    if endpoint == "/ask":
                        self.assertEqual(response.json()["conversation_context"]["view_ids"], list(selected))
                    else:
                        events = parse_events(response.text)
                        self.assertEqual([payload["view_id"] for event, payload in events if event == "view_result"], list(selected))
                        self.assertEqual(events[-1][0], "done")

    def test_regions_clarification_then_all_reuses_three_sources_before_classification(self):
        question = "total no.of regions and list of regions"
        source_ids = ("dw_ms_crm_onprem.ntw_regionBase", "nsp_base_view_01", "population_estimates_statistics_view_01")
        crm = ApprovedTable("dw_ms_crm_onprem", "ntw_regionBase", "CRM regions", "Regions", "CRM region list")
        queries = [f"SELECT `region` FROM `{source.full_id}` LIMIT 10" for source in (
            crm, get_view(source_ids[1]), get_view(source_ids[2]),
        )]
        for endpoint in ("/ask", "/ask/stream"):
            for reply, expected_ids in (("all", source_ids), ("provide for all", source_ids),
                                        ("third", source_ids[2:]), ("first and second", source_ids[:2]),
                                        ("الكل", source_ids)):
                with (
                    self.subTest(endpoint=endpoint, reply=reply),
                    patch("view_catalog.get_authorized_tables", return_value=(crm,)),
                    patch("view_router.route_question", return_value=RouteResult(
                        clarification="Choose: 1. CRM regions 2. NSP athlete registrations 3. Population statistics",
                        candidate_view_ids=source_ids,
                    )) as route,
                    patch("app.is_metadata_question", return_value=False) as classify,
                    patch("gemini_service.generate_sql", side_effect=lambda original, language, source_id, history: queries[source_ids.index(source_id)]) as generate,
                    patch("sql_guardrails.get_view_columns", return_value=[("region", "STRING", "NULLABLE")]),
                    patch("bigquery_database.client.query") as query,
                    patch("app.generate_answer_stream", side_effect=lambda *args: iter(["Regions: fixture region"])),
                    patch("app.generate_answer", return_value="Regions: fixture region"),
                ):
                    query.return_value.total_bytes_processed = 1
                    result = query.return_value.result.return_value
                    result.schema = [SimpleNamespace(name="region")]
                    result.__iter__.side_effect = lambda: iter([{"region": "fixture region"}])
                    initial = self.client.post(endpoint, json={"question": question})
                    self.assertEqual(initial.status_code, 200)
                    context = initial.json()["conversation_context"] if endpoint == "/ask" else next(
                        payload["conversation_context"] for name, payload in parse_events(initial.text) if name == "context"
                    )
                    self.assertEqual(context["pending"]["question"], question)
                    self.assertEqual([option["view_id"] for option in context["pending"]["options"]], list(source_ids))
                    query.assert_not_called()
                    response = self.client.post(endpoint, json={"question": reply, "conversation_context": context})
                    self.assertEqual(response.status_code, 200)
                    route.assert_called_once()
                    classify.assert_called_once()
                    self.assertEqual([call.args[0] for call in generate.call_args_list], [question] * len(expected_ids))
                    self.assertEqual([call.args[2] for call in generate.call_args_list], list(expected_ids))
                    executions = [call for call in query.call_args_list if not call.kwargs["job_config"].dry_run]
                    self.assertEqual(len(executions), len(expected_ids))
                    for call in executions:
                        self.assertEqual(call.kwargs["job_config"].maximum_bytes_billed, 262144000)
                    if endpoint.endswith("stream"):
                        events = parse_events(response.text)
                        self.assertEqual([payload["view_id"] for name, payload in events if name == "view_result"], list(expected_ids))
                        self.assertEqual(events[-1][0], "done")
                    else:
                        self.assertEqual(response.json()["conversation_context"]["view_ids"], list(expected_ids))
                    self.assertEqual(response.text.count("Regions: fixture region") >= len(expected_ids), True)

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
        view_results = [payload for name, payload in parse_events(response.text) if name == "view_result"]
        self.assertEqual([result["view_id"] for result in view_results], ["nsp_base_view_01", "sopc_view_01"])
        self.assertEqual([result["display_name"] for result in view_results], [
            "NSP Base View",
            "Sports Competitions and Athlete Performance View",
        ])
        self.assertNotIn("### NSP Base View", view_results[0]["markdown"])
        self.assertNotIn("### Sports Competitions and Athlete Performance View", view_results[1]["markdown"])
        self.assertIn("Athletes: 12", view_results[0]["markdown"])
        self.assertIn("Athletes: 7", view_results[1]["markdown"])
        self.assertIn('event: done\ndata: {"question": "both"}', response.text)

    def test_dynamic_source_passes_real_validator_only_with_policy_authorization(self):
        source = DiscoveredSource(
            project=BIGQUERY_PROJECT_ID,
            dataset="dw_sharepoint",
            name="budget_dynamic_view",
            object_type="VIEW",
            description="Budget source",
            columns=(ColumnMetadata("budget_total", "NUMERIC", "NULLABLE"),),
            query_authorized=True,
        )
        sql = f"SELECT `budget_total` FROM `{source.full_id}`"
        allowed_plan = QueryPlan(queries=((source.source_id, sql),))
        with (
            patch("app.generate_query_plan", return_value=allowed_plan),
            patch("view_catalog.BIGQUERY_DYNAMIC_ALLOWED_DATASETS", frozenset({"dw_sharepoint"})),
            patch("view_catalog.get_authorized_tables", return_value=()),
            patch("view_catalog.get_dynamic_authorized_sources", return_value=(source,)),
            patch("app.execute_sql_batch", return_value=[(["budget_total"], [(1250000,)])]) as execute,
            patch("app.generate_answer_stream", return_value=iter(["Budget total: 1250000"])),
        ):
            response = self.client.post("/ask/stream", json={"question": "What is the budget?"})

        self.assertIn("Budget total: 1250000", response.text)
        self.assertIn('"object_type": "VIEW"', response.text)
        self.assertIn('event: done', response.text)
        execute.assert_called_once_with([sql + " LIMIT 100"])

        denied_plan = QueryPlan(queries=(("dw_sharepoint.unlisted_view", sql.replace(source.table if hasattr(source, "table") else source.name, "unlisted_view")),))
        with (
            patch("app.generate_query_plan", return_value=denied_plan),
            patch("view_catalog.BIGQUERY_DYNAMIC_ALLOWED_DATASETS", frozenset({"dw_sharepoint"})),
            patch("view_catalog.get_authorized_tables", return_value=()),
            patch("view_catalog.get_dynamic_authorized_sources", return_value=(source,)),
            patch("app.execute_sql_batch") as denied_execute,
        ):
            denied = self.client.post("/ask/stream", json={"question": "unlisted"})

        denied_execute.assert_not_called()
        self.assertIn(app.SQL_VALIDATION_MESSAGES["en"], denied.text)
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

    def test_bare_explanation_follow_ups_never_plan_or_execute_queries(self):
        history = [{"role": "assistant", "text": "Football: 12", "is_result": True}]
        for question in ("explain", "why?", "اشرح", "لماذا؟"):
            for endpoint in ("/ask", "/ask/stream"):
                with (
                    self.subTest(question=question, endpoint=endpoint),
                    patch("app.generate_query_plan") as plan,
                    patch("app.execute_sql_batch") as execute,
                    patch("app.explain_previous_results", return_value="Explanation based on Football: 12") as explain,
                ):
                    response = self.client.post(endpoint, json={"question": question, "conversation_history": history})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("Explanation based on Football: 12", response.text)
                    if endpoint.endswith("stream"):
                        self.assertIn("event: done", response.text)
                    plan.assert_not_called()
                    execute.assert_not_called()
                    explain.assert_called_once()

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

    def test_json_budget_exceeded_is_explained_without_answer_generation(self):
        plan = self.plan(self.sql)
        with (
            patch("app.generate_query_plan", return_value=plan),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql_batch", side_effect=QueryBudgetExceeded("combined cap")) as execute,
            patch("app.generate_answer") as answer,
        ):
            response = self.client.post("/ask", json={"question": self.question})

        self.assertEqual(response.status_code, 200)
        self.assertIn("combined BigQuery estimate exceeds the 250 MiB limit", response.json()["answer"])
        execute.assert_called_once()
        answer.assert_not_called()

    def test_sse_budget_exceeded_emits_message_and_done_without_query_results(self):
        with (
            patch("app.generate_query_plan", return_value=self.plan(self.sql)),
            patch("app.validate_sql", side_effect=lambda query: query),
            patch("app.execute_sql_batch", side_effect=QueryBudgetExceeded("combined cap")),
            patch("app.generate_answer_stream") as answer,
        ):
            response = self.client.post("/ask/stream", json={"question": self.question})

        self.assertIn("combined BigQuery estimate exceeds the 250 MiB limit", response.text)
        self.assertIn("event: done", response.text)
        self.assertNotIn("event: error", response.text)
        answer.assert_not_called()

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
        self.assertEqual(response.json()["answer"], app.SQL_VALIDATION_MESSAGES["en"])

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
        self.assertEqual(response.json()["answer"], app.SQL_VALIDATION_MESSAGES["en"])

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
        self.assertEqual(response.json()["answer"], app.SQL_VALIDATION_MESSAGES["en"])
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
        self.assertIn(app.SQL_VALIDATION_MESSAGES["en"], response.text)
        self.assertNotIn("information_schema", response.text)
        self.assertNotIn("event: error", response.text)


if __name__ == "__main__":
    unittest.main()
