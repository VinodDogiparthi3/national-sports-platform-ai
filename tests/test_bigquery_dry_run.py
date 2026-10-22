import unittest
from unittest.mock import Mock, patch

from bigquery_database import dry_run_sql
from config import MAX_BYTES_PROCESSED


class BigQueryDryRunTests(unittest.TestCase):
	def test_dry_run_uses_query_cache_off_and_enforces_exact_cap(self):
		query_job = Mock(total_bytes_processed=1024)
		with patch("bigquery_database.client.query", return_value=query_job) as query:
			estimate = dry_run_sql("SELECT 1")

		self.assertEqual(estimate, 1024)
		job_config = query.call_args.kwargs["job_config"]
		self.assertTrue(job_config.dry_run)
		self.assertFalse(job_config.use_query_cache)
		self.assertEqual(MAX_BYTES_PROCESSED, 262_144_000)


if __name__ == "__main__":
	unittest.main()