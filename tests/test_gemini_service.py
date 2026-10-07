import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gemini_service import OUT_OF_SCOPE_CLARIFICATION, generate_sql


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


if __name__ == "__main__":
    unittest.main()