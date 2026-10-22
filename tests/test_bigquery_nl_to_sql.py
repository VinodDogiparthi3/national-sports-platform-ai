import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config import BIGQUERY_DATASET_ID, BIGQUERY_PROJECT_ID, BIGQUERY_TABLE_ID
from gemini_service import generate_sql


class NaturalLanguageToSqlTests(unittest.TestCase):
	def test_generation_uses_supplied_schema_and_mocked_gemini_response(self):
		question = "How many registrations are there for each sport?"
		generated = f"SELECT sport FROM `{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}`"
		with (
			patch("gemini_service.get_bigquery_schema", return_value="Fixture schema") as schema,
			patch("gemini_service.client.models.generate_content", return_value=SimpleNamespace(text=generated)) as model,
			patch("gemini_service.get_filter_values", return_value={}),
		):
			result = generate_sql(question, view_id=BIGQUERY_TABLE_ID)

		self.assertEqual(result, generated)
		schema.assert_called_once_with(BIGQUERY_TABLE_ID)
		model.assert_called_once()


if __name__ == "__main__":
	unittest.main()