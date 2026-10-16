import unittest
from types import SimpleNamespace
from unittest.mock import patch

from conversation_context import (
    is_explanation_request,
    previous_result_text,
    resolve_pending_selection,
    translation_target,
)
from gemini_service import explain_previous_results, translate_result_preserving_numbers
from gemini_service import generate_query_plan
from view_router import RouteResult


class PendingSelectionTests(unittest.TestCase):
    def setUp(self):
        self.views = {
            "pending": {
                "kind": "views",
                "question": "How many athlete registrations are there?",
                "options": [
                    {"view_id": "nsp_base_view_01"},
                    {"view_id": "sopc_view_01"},
                ],
            }
        }

    def test_both_and_all_select_each_approved_view(self):
        for reply in ("both", "all", "كلاهما", "الكل"):
            with self.subTest(reply=reply):
                result = resolve_pending_selection(reply, self.views)
                self.assertEqual(result["view_ids"], ["nsp_base_view_01", "sopc_view_01"])
                self.assertEqual(result["question"], self.views["pending"]["question"])

    def test_first_and_second_select_one_view(self):
        self.assertEqual(resolve_pending_selection("first", self.views)["view_ids"], ["nsp_base_view_01"])
        self.assertEqual(resolve_pending_selection("الأول", self.views)["view_ids"], ["nsp_base_view_01"])
        self.assertEqual(resolve_pending_selection("الاول", self.views)["view_ids"], ["nsp_base_view_01"])
        self.assertEqual(resolve_pending_selection("الثاني", self.views)["view_ids"], ["sopc_view_01"])

    def test_year_selection_preserves_question_and_selects_matching_view(self):
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

        result = resolve_pending_selection("٢٠٢٤", context)

        self.assertEqual(result["view_ids"], ["population_estimates_statistics_view_01"])
        self.assertEqual(result["question"], "Show population by age group for 2024")

    def test_unapproved_view_id_in_client_context_is_rejected(self):
        context = {
            "pending": {
                "kind": "views",
                "question": "Show registrations",
                "options": [{"view_id": "dw_ms_crm_onprem.not_authorized"}],
            }
        }
        with patch("conversation_context.get_source", return_value=None):
            self.assertIsNone(resolve_pending_selection("all", context))


class FollowUpIntentTests(unittest.TestCase):
    def test_translation_target_uses_explicit_language_or_opposite_ui_language(self):
        self.assertEqual(translation_target("translate the above results to English", "ar"), "en")
        self.assertEqual(translation_target("ترجم النتائج السابقة إلى العربية", "en"), "ar")
        self.assertEqual(translation_target("translate previous results", "ar"), "en")

    def test_new_translation_request_requires_a_previous_response_reference(self):
        self.assertIsNone(translation_target("translate this question", "en"))

    def test_explanation_intent_requires_previous_result_reference(self):
        self.assertTrue(is_explanation_request("Explain the previous results"))
        self.assertTrue(is_explanation_request("اشرح النتائج السابقة"))
        self.assertFalse(is_explanation_request("Explain the registration process"))

    def test_previous_result_skips_clarification_messages(self):
        history = [
            {"role": "assistant", "text": "Results: 13", "is_result": True},
            {"role": "assistant", "text": "Which view did you mean?", "is_result": False},
        ]
        self.assertEqual(previous_result_text(history), "Results: 13")

    @patch("gemini_service.translate_texts")
    def test_translation_restores_numeric_values_exactly_with_one_call(self, translate):
        translate.side_effect = lambda texts, _language: ["تمت الترجمة: " + texts[0]]

        result = translate_result_preserving_numbers("Total: 13,650 in 2024 (0.55%).", "ar")

        self.assertIn("13,650", result)
        self.assertIn("2024", result)
        self.assertIn("0.55%", result)
        translate.assert_called_once()

    @patch("gemini_service.client.models.generate_content")
    def test_explanation_uses_previous_displayed_results(self, generate_content):
        generate_content.return_value = SimpleNamespace(text="The second value is larger.")

        result = explain_previous_results("Explain the previous results", "A: 13; B: 21", "en")

        self.assertEqual(result, "The second value is larger.")
        self.assertIn("A: 13; B: 21", generate_content.call_args.kwargs["contents"])
        self.assertEqual(generate_content.call_count, 1)



class QueryPlanContextTests(unittest.TestCase):
    def test_both_generates_separate_sql_without_rerouting(self):
        context = {
            "pending": {
                "kind": "views",
                "question": "How many athletes are registered?",
                "options": [
                    {"view_id": "nsp_base_view_01"},
                    {"view_id": "sopc_view_01"},
                ],
            }
        }
        with (
            patch("view_router.route_question") as route,
            patch("gemini_service.generate_sql", side_effect=["SELECT 1", "SELECT 2"]) as generate_sql,
        ):
            plan = generate_query_plan("both", "en", conversation_context=context)

        route.assert_not_called()
        self.assertEqual(plan.queries, (("nsp_base_view_01", "SELECT 1"), ("sopc_view_01", "SELECT 2")))
        self.assertEqual(generate_sql.call_count, 2)

    def test_selected_year_resolves_original_request_and_view(self):
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
        ):
            plan = generate_query_plan("2024", "en", conversation_context=context)

        route.assert_not_called()
        generate_sql.assert_called_once_with(
            "Show population by age group for 2024",
            "en",
            "population_estimates_statistics_view_01",
            (),
        )
        self.assertEqual(plan.queries[0][0], "population_estimates_statistics_view_01")

    def test_ambiguous_route_keeps_candidate_ids_in_pending_context(self):
        route_result = RouteResult(
            clarification="Which view?",
            candidate_view_ids=("nsp_base_view_01", "sopc_view_01"),
        )
        with (
            patch("view_router.route_question", return_value=route_result),
            patch("gemini_service.generate_sql") as generate_sql,
        ):
            plan = generate_query_plan("How many athletes?", "en")

        generate_sql.assert_not_called()
        self.assertEqual(plan.conversation_context["pending"]["kind"], "views")
        self.assertEqual(
            [option["view_id"] for option in plan.conversation_context["pending"]["options"]],
            ["nsp_base_view_01", "sopc_view_01"],
        )

    def test_unrelated_follow_up_is_routed_independently(self):
        previous_context = {
            "pending": {"kind": "views", "question": "Athletes?", "options": [{"view_id": "nsp_base_view_01"}]},
            "last_question": "Athletes?",
            "view_ids": ["nsp_base_view_01"],
        }
        history = [{"role": "assistant", "text": "Choose a view", "is_result": False}]
        with (
            patch("view_router.route_question", return_value=RouteResult(clarification="No matching view.")) as route,
            patch("gemini_service.generate_sql") as generate_sql,
        ):
            plan = generate_query_plan("What is the weather?", "en", history, previous_context)

        self.assertEqual(plan.clarification, "No matching view.")
        route.assert_called_once_with("What is the weather?", "en", history)
        generate_sql.assert_not_called()

    def test_related_follow_up_passes_conversation_history_to_sql_generation(self):
        history = [
            {"role": "user", "text": "How many registrations are there for each sport?"},
            {"role": "assistant", "text": "Football: 12", "is_result": True},
        ]
        with (
            patch("view_router.route_question", return_value=RouteResult(source_id="nsp_base_view_01")) as route,
            patch("gemini_service.generate_sql", return_value="SELECT 1") as generate_sql,
        ):
            plan = generate_query_plan("And for women?", "en", history)

        self.assertEqual(plan.queries, (("nsp_base_view_01", "SELECT 1"),))
        route.assert_called_once_with("And for women?", "en", history)
        generate_sql.assert_called_once_with("And for women?", "en", "nsp_base_view_01", history)


if __name__ == "__main__":
    unittest.main()