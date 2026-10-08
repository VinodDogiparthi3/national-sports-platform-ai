import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gemini_service import (
    OUT_OF_SCOPE_CLARIFICATION,
    _build_answer_prompt,
    extract_export_analysis_question,
    generate_sql,
    translate_texts,
    translate_questions_to_arabic,
)


class GenerateSqlTests(unittest.TestCase):
    @patch("gemini_service.get_bigquery_schema", return_value="schema")
    @patch("gemini_service.client.models.generate_content")
    def test_removes_sql_code_fence(self, generate_content, _get_bigquery_schema):
        generate_content.return_value = SimpleNamespace(
            text="```sql\nSELECT 1\n```"
        )

        self.assertEqual(generate_sql("question"), "SELECT 1")

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

    def test_arabic_answer_prompt_preserves_english_database_values(self):
        prompt = _build_answer_prompt(
            "ما الرياضة الأكثر تسجيلًا؟",
            ["sport"],
            [("Football",)],
            "ar",
        )

        self.assertIn("in Arabic only", prompt)
        self.assertIn("Do not translate, transliterate, or modify those values", prompt)
        self.assertIn("Football", prompt)
        self.assertIn("**إجمالي التسجيلات:**", prompt)

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


if __name__ == "__main__":
    unittest.main()