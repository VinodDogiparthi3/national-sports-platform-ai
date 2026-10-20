from datetime import datetime
from pathlib import Path
from typing import Protocol

from uc2.models import AnalysisPolicy, AnalysisRequest, Application, SummaryRequest, SyntheticDataset


class WorkflowRepository(Protocol):
    fixture_version: str
    policy: AnalysisPolicy
    assumptions: tuple[str, ...]

    def list_applications(self, received_month: str, as_of: datetime) -> tuple[Application, ...]: ...

    def get_application(self, application_id: str, as_of: datetime) -> Application: ...


class SyntheticRepository:
    def __init__(self, dataset: SyntheticDataset | None = None):
        self._dataset = dataset if dataset is not None else SyntheticDataset.model_validate_json(
            (Path(__file__).parent / "data" / "synthetic_workflows.v1.json").read_text(encoding="utf-8")
        )
        self.fixture_version = self._dataset.fixture_version
        self.policy = self._dataset.policy
        self.assumptions = self._dataset.assumptions

    def list_applications(self, received_month: str, as_of: datetime) -> tuple[Application, ...]:
        request = SummaryRequest(received_month=received_month, as_of=as_of)
        return tuple(
            self._snapshot(application, request.as_of)
            for application in sorted(self._dataset.applications, key=lambda item: item.application_id)
            if application.received_at.strftime("%Y-%m") == request.received_month
            and application.received_at <= request.as_of
        )

    def get_application(self, application_id: str, as_of: datetime) -> Application:
        cutoff = AnalysisRequest(as_of=as_of).as_of
        for application in self._dataset.applications:
            if application.application_id == application_id and application.received_at <= cutoff:
                return self._snapshot(application, cutoff)
        raise KeyError(application_id)

    @staticmethod
    def _snapshot(application: Application, as_of: datetime) -> Application:
        data = application.model_dump()
        visits = []
        for visit in data["stage_visits"]:
            if visit["entered_at"] is not None and visit["entered_at"] > as_of:
                continue
            if visit["exited_at"] is not None and visit["exited_at"] > as_of:
                visit["exited_at"] = None
            blockers = []
            for blocker in visit["blocking_intervals"]:
                if blocker["started_at"] > as_of:
                    continue
                if blocker["ended_at"] is not None and blocker["ended_at"] > as_of:
                    blocker["ended_at"] = None
                blockers.append(blocker)
            visit["blocking_intervals"] = blockers
            visits.append(visit)
        visit_ids = {visit["visit_id"] for visit in visits}
        evidence = [
            item for item in data["evidence"]
            if item["recorded_at"] <= as_of and item["effective_at"] <= as_of
            and (item["visit_id"] is None or item["visit_id"] in visit_ids)
        ]
        evidence_ids = {item["evidence_id"] for item in evidence}
        for visit in visits:
            visit["predecessor_visit_ids"] = tuple(identifier for identifier in visit["predecessor_visit_ids"] if identifier in visit_ids)
            visit["evidence_ids"] = tuple(identifier for identifier in visit["evidence_ids"] if identifier in evidence_ids)
            for blocker in visit["blocking_intervals"]:
                blocker["evidence_ids"] = tuple(identifier for identifier in blocker["evidence_ids"] if identifier in evidence_ids)
        data["stage_visits"] = visits
        data["evidence"] = evidence
        if application.completed_at and application.completed_at > as_of:
            data["completed_at"] = None
            data["status"] = "open"
        return Application.model_validate(data)