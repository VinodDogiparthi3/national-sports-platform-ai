import unittest

from pydantic import ValidationError

from uc2.models import Application, BlockerInterval, StageVisit, SummaryRequest


class ModelTests(unittest.TestCase):
    def test_timestamps_require_timezones_and_normalize_to_utc(self):
        request = SummaryRequest(received_month="2026-10", as_of="2026-11-01T03:00:00+03:00")
        self.assertEqual(request.as_of.isoformat(), "2026-11-01T00:00:00+00:00")
        with self.assertRaises(ValidationError):
            SummaryRequest(received_month="2026-10", as_of="2026-11-01T00:00:00")

    def test_invalid_month_and_reversed_blocker_are_rejected(self):
        with self.assertRaises(ValidationError):
            SummaryRequest(received_month="2026-13", as_of="2026-11-01T00:00:00Z")
        with self.assertRaises(ValidationError):
            BlockerInterval(started_at="2026-10-02T00:00:00Z", ended_at="2026-10-01T00:00:00Z")

    def test_missing_stage_timestamps_remain_representable(self):
        visit = StageVisit(visit_id="review-1", stage_code="review")
        self.assertIsNone(visit.entered_at)
        self.assertIsNone(visit.exited_at)

    def test_reversed_stage_and_unknown_evidence_are_rejected(self):
        with self.assertRaises(ValidationError):
            StageVisit(visit_id="review-1", stage_code="review", entered_at="2026-10-02T00:00:00Z", exited_at="2026-10-01T00:00:00Z")
        with self.assertRaises(ValidationError):
            Application(application_id="SYN-X", workflow_instance_id="WF-X", received_at="2026-10-01T00:00:00Z", status="open", stage_visits=[{
                "visit_id": "review-1", "stage_code": "review", "evidence_ids": ["missing"],
            }])

    def test_cycles_are_rejected_even_when_timestamps_are_missing(self):
        with self.assertRaises(ValidationError):
            Application(application_id="SYN-X", workflow_instance_id="WF-X", received_at="2026-10-01T00:00:00Z", status="open", stage_visits=[
                {"visit_id": "first", "stage_code": "review", "predecessor_visit_ids": ["second"]},
                {"visit_id": "second", "stage_code": "approval", "predecessor_visit_ids": ["first"]},
            ])