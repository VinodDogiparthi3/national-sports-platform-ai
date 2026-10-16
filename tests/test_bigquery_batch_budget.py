import unittest
from unittest.mock import patch

from config import HARD_MAX_BYTES_PROCESSED, MAX_BYTES_PROCESSED, bounded_max_bytes
from bigquery_database import execute_sql_batch, get_export_row_counts, stream_query_rows_batch


class BatchBudgetTests(unittest.TestCase):
    def test_environment_value_cannot_raise_the_hard_cap(self):
        self.assertEqual(MAX_BYTES_PROCESSED, HARD_MAX_BYTES_PROCESSED)
        self.assertEqual(bounded_max_bytes("999999999"), 262_144_000)
        self.assertEqual(bounded_max_bytes("1048576"), 1_048_576)

    @patch("bigquery_database.dry_run_sql", side_effect=[100, 200])
    @patch("bigquery_database.client.query")
    def test_executes_all_queries_after_preflighting_under_budget(self, query, dry_run):
        result = query.return_value.result.return_value
        result.schema = []
        result.__iter__.return_value = iter(())

        output = execute_sql_batch(["SELECT 1", "SELECT 2"])

        self.assertEqual(output, [([], []), ([], [])])
        self.assertEqual(dry_run.call_count, 2)
        self.assertEqual(query.call_count, 2)
        for call in query.call_args_list:
            self.assertEqual(call.kwargs["job_config"].maximum_bytes_billed, MAX_BYTES_PROCESSED)

    @patch(
        "bigquery_database.dry_run_sql",
        side_effect=[MAX_BYTES_PROCESSED // 2 + 1, MAX_BYTES_PROCESSED // 2 + 1],
    )
    @patch("bigquery_database.client.query")
    def test_rejects_combined_over_budget_before_executing_any_query(self, query, dry_run):
        with self.assertRaisesRegex(ValueError, "Combined queries exceed"):
            execute_sql_batch(["SELECT 1", "SELECT 2"])

        self.assertEqual(dry_run.call_count, 2)
        query.assert_not_called()

    @patch("bigquery_database.dry_run_sql", side_effect=[100, 200])
    @patch("bigquery_database.client.query")
    def test_export_counts_use_one_combined_preflight(self, query, dry_run):
        query.return_value.result.return_value.total_rows = 3

        self.assertEqual(get_export_row_counts(["SELECT 1", "SELECT 2"]), [3, 3])

        self.assertEqual(dry_run.call_count, 2)
        self.assertEqual(query.call_count, 2)
        for call in query.call_args_list:
            self.assertEqual(call.kwargs["job_config"].maximum_bytes_billed, MAX_BYTES_PROCESSED)

    @patch(
        "bigquery_database.dry_run_sql",
        side_effect=[MAX_BYTES_PROCESSED // 2 + 1, MAX_BYTES_PROCESSED // 2 + 1],
    )
    @patch("bigquery_database.client.query")
    def test_download_batch_rejects_combined_over_budget_before_streaming(self, query, dry_run):
        with self.assertRaisesRegex(ValueError, "Combined queries exceed"):
            stream_query_rows_batch(["SELECT 1", "SELECT 2"])

        self.assertEqual(dry_run.call_count, 2)
        query.assert_not_called()

    @patch("bigquery_database.dry_run_sql", return_value=MAX_BYTES_PROCESSED // 2 + 1)
    @patch("bigquery_database.client.query")
    def test_export_rejects_count_plus_download_over_budget_before_counting(self, query, dry_run):
        with self.assertRaisesRegex(ValueError, "preparation and download"):
            get_export_row_counts(["SELECT 1"])

        dry_run.assert_called_once_with("SELECT 1")
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()