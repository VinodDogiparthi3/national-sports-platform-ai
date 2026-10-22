import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from bigquery_database import execute_sql
from config import MAX_BYTES_PROCESSED


class BigQueryExecuteTests(unittest.TestCase):
    def test_execute_dry_runs_first_and_sets_maximum_bytes_billed(self):
        row = SimpleNamespace(values=lambda: ["Fixture sport"])
        query_job = Mock()
        query_result = MagicMock()
        query_result.schema = [SimpleNamespace(name="sport")]
        query_result.__iter__.return_value = iter([row])
        query_job.result.return_value = query_result
        with (
            patch("bigquery_database.dry_run_sql", return_value=10) as dry_run,
            patch("bigquery_database.client.query", return_value=query_job) as query,
        ):
            columns, rows = execute_sql("SELECT sport FROM fixture")

        dry_run.assert_called_once_with("SELECT sport FROM fixture")
        self.assertEqual(columns, ["sport"])
        self.assertEqual(rows, [("Fixture sport",)])
        self.assertEqual(query.call_args.kwargs["job_config"].maximum_bytes_billed, MAX_BYTES_PROCESSED)


if __name__ == "__main__":
    unittest.main()