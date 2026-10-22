import unittest
from unittest.mock import patch

from gemini_service import QueryPlan


class BigQueryFullFlowTests(unittest.TestCase):
    def test_question_sql_validation_execution_and_answer_are_mocked(self):
        question = "How many registrations are there for each sport?"
        generated_sql = "SELECT sport FROM `mos-gcp-host-dev-prj.dw_nsp_api_views.nsp_base_view_01`"
        with (
            patch("app.generate_query_plan", return_value=QueryPlan(queries=(("nsp_base_view_01", generated_sql),))) as plan,
            patch("app.validate_sql", return_value=generated_sql) as validate,
            patch("app.execute_sql_batch", return_value=[(["sport"], [("Fixture sport",)])]) as execute,
            patch("app.generate_answer", return_value="Fixture answer") as answer,
        ):
            from app import ask
            from app import QuestionRequest
            response = ask(QuestionRequest(question=question))

        self.assertEqual(response["answer"], "Fixture answer")
        plan.assert_called_once()
        validate.assert_called_once_with(generated_sql)
        execute.assert_called_once_with([generated_sql])
        answer.assert_called_once_with(question, ["sport"], [("Fixture sport",)], "en")


if __name__ == "__main__":
    unittest.main()