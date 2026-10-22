import unittest
from unittest.mock import patch

from config import BIGQUERY_DATASET_ID, BIGQUERY_PROJECT_ID, BIGQUERY_TABLE_ID
from sql_guardrails import OutOfScopeQueryError, validate_sql


class SqlGuardrailsTests(unittest.TestCase):
	def setUp(self):
		self.full_table_id = f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"

	def test_approved_select_is_capped_and_no_client_is_created(self):
		sql = f"SELECT sport FROM `{self.full_table_id}`"
		with patch("sql_guardrails.get_allowed_columns", return_value={"sport"}):
			self.assertEqual(validate_sql(sql), f"SELECT sport FROM `{self.full_table_id}` LIMIT 100")

	def test_unknown_column_and_unapproved_project_are_rejected(self):
		with patch("sql_guardrails.get_allowed_columns", return_value={"sport"}):
			with self.assertRaises(OutOfScopeQueryError):
				validate_sql(f"SELECT secret FROM `{self.full_table_id}`")
			with self.assertRaises(OutOfScopeQueryError):
				validate_sql("SELECT sport FROM `other-project.other-dataset.other-table`")

	def test_mutating_and_multiple_statements_are_rejected(self):
		with self.assertRaisesRegex(ValueError, "Only SELECT"):
			validate_sql(f"DELETE FROM `{self.full_table_id}`")
		with self.assertRaisesRegex(ValueError, "Multiple SQL statements"):
			validate_sql(f"SELECT sport FROM `{self.full_table_id}`; SELECT 1")


if __name__ == "__main__":
	unittest.main()