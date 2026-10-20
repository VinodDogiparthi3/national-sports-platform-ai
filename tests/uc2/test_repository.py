import unittest
from datetime import datetime, timezone

from uc2.repository import SyntheticRepository


AS_OF = datetime(2026, 11, 1, tzinfo=timezone.utc)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = SyntheticRepository()

    def test_fixture_has_four_fictional_applications_with_expected_durations(self):
        applications = self.repository.list_applications("2026-10", AS_OF)
        self.assertEqual([item.application_id for item in applications], ["SYN-A", "SYN-B", "SYN-C", "SYN-D"])
        self.assertEqual([(item.completed_at - item.received_at).total_seconds() / 3600 for item in applications], [48, 72, 60, 600])
        self.assertTrue(all(evidence.synthetic for item in applications for evidence in item.evidence))

    def test_cutoff_hides_future_visits_evidence_completion_and_blocker_end(self):
        cutoff = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)
        application = self.repository.get_application("SYN-D", cutoff)
        self.assertEqual(application.status, "open")
        self.assertIsNone(application.completed_at)
        self.assertEqual(len(application.stage_visits), 3)
        approval = application.stage_visits[-1]
        self.assertIsNone(approval.exited_at)
        self.assertIsNone(approval.blocking_intervals[0].ended_at)
        self.assertNotIn("SYN-D-VERIFY-BLOCK-END", approval.blocking_intervals[0].evidence_ids)
        self.assertTrue(all(item.recorded_at <= cutoff and item.effective_at <= cutoff for item in application.evidence))

    def test_unknown_application_and_receipt_after_cutoff_are_not_visible(self):
        with self.assertRaises(KeyError):
            self.repository.get_application("missing", AS_OF)
        with self.assertRaises(KeyError):
            self.repository.get_application("SYN-A", datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual(self.repository.list_applications("2026-09", AS_OF), ())

    def test_snapshot_does_not_mutate_later_reads(self):
        self.repository.get_application("SYN-D", datetime(2026, 10, 3, 9, tzinfo=timezone.utc))
        self.assertEqual(len(self.repository.get_application("SYN-D", AS_OF).stage_visits), 4)