from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone.")
    return value.astimezone(timezone.utc)


Timestamp = Annotated[datetime, AfterValidator(utc_timestamp)]
Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]
Hours = Annotated[float, Field(ge=0, allow_inf_nan=False)]
SlaStatus = Literal["within_sla", "breached", "unknown"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BlockerInterval(Contract):
    started_at: Timestamp
    ended_at: Timestamp | None = None
    evidence_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_interval(self):
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("Blocker end precedes its start.")
        return self


class Evidence(Contract):
    evidence_id: Identifier
    application_id: Identifier
    workflow_instance_id: Identifier
    visit_id: Identifier | None = None
    kind: Literal["transition", "blocker_start", "blocker_end", "note"]
    recorded_at: Timestamp
    effective_at: Timestamp
    text: str = Field(min_length=1, max_length=4000)
    synthetic: Literal[True] = True


class StageVisit(Contract):
    visit_id: Identifier
    stage_code: Literal["intake", "review", "approval", "issuance"]
    attempt: int = Field(default=1, ge=1)
    entered_at: Timestamp | None = None
    exited_at: Timestamp | None = None
    predecessor_visit_ids: tuple[Identifier, ...] = ()
    blocking_intervals: tuple[BlockerInterval, ...] = ()
    evidence_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_visit(self):
        if self.entered_at and self.exited_at and self.exited_at < self.entered_at:
            raise ValueError("Stage exit precedes entry.")
        if len(set(self.predecessor_visit_ids)) != len(self.predecessor_visit_ids):
            raise ValueError("Duplicate stage dependencies.")
        for blocker in self.blocking_intervals:
            if self.entered_at and blocker.started_at < self.entered_at:
                raise ValueError("Blocker starts outside its stage visit.")
            if self.exited_at and (blocker.ended_at is None or blocker.ended_at > self.exited_at):
                raise ValueError("Blocker ends outside its stage visit.")
            if self.exited_at and blocker.started_at > self.exited_at:
                raise ValueError("Blocker starts after stage exit.")
        return self


class Application(Contract):
    application_id: Identifier
    license_id: Identifier | None = None
    received_at: Timestamp
    completed_at: Timestamp | None = None
    status: Literal["completed", "open"]
    workflow_instance_id: Identifier
    stage_visits: tuple[StageVisit, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def validate_workflow(self):
        if self.completed_at and self.completed_at < self.received_at:
            raise ValueError("Completion precedes receipt.")
        if self.status == "open" and self.completed_at:
            raise ValueError("An open application cannot have a completion timestamp.")
        visits = {visit.visit_id: visit for visit in self.stage_visits}
        evidence = {item.evidence_id: item for item in self.evidence}
        if len(visits) != len(self.stage_visits) or len(evidence) != len(self.evidence):
            raise ValueError("Duplicate visit or evidence identifiers.")
        attempts = [(visit.stage_code, visit.attempt) for visit in self.stage_visits]
        if len(set(attempts)) != len(attempts):
            raise ValueError("Repeated stages require distinct attempt numbers.")
        for item in self.evidence:
            if (item.application_id, item.workflow_instance_id) != (self.application_id, self.workflow_instance_id):
                raise ValueError("Evidence belongs to another application or workflow.")
            if item.visit_id is not None and item.visit_id not in visits:
                raise ValueError("Evidence references an unknown visit.")
            if min(item.effective_at, item.recorded_at) < self.received_at:
                raise ValueError("Evidence precedes application receipt.")
            if item.recorded_at < item.effective_at:
                raise ValueError("Recorded workflow evidence cannot precede its effective event.")
            if self.completed_at and item.effective_at > self.completed_at:
                raise ValueError("Evidence event occurs after application completion.")
            if item.visit_id is not None:
                visit = visits[item.visit_id]
                if (visit.entered_at and item.effective_at < visit.entered_at) or (
                    visit.exited_at and item.effective_at > visit.exited_at
                ):
                    raise ValueError("Evidence event is outside its stage visit.")
        for visit in self.stage_visits:
            timestamps = [visit.entered_at, visit.exited_at]
            timestamps.extend(blocker.started_at for blocker in visit.blocking_intervals)
            timestamps.extend(blocker.ended_at for blocker in visit.blocking_intervals)
            for timestamp in filter(None, timestamps):
                if timestamp < self.received_at or (self.completed_at and timestamp > self.completed_at):
                    raise ValueError("Stage or blocker timestamp is outside the application interval.")
            references = [(visit.evidence_ids, visit)]
            references.extend((blocker.evidence_ids, visit) for blocker in visit.blocking_intervals)
            for identifiers, owner in references:
                if len(set(identifiers)) != len(identifiers):
                    raise ValueError("Duplicate evidence references.")
                for identifier in identifiers:
                    item = evidence.get(identifier)
                    if item is None or item.visit_id != owner.visit_id:
                        raise ValueError("Unknown or cross-visit evidence reference.")
            for predecessor_id in visit.predecessor_visit_ids:
                predecessor = visits.get(predecessor_id)
                if predecessor is None:
                    raise ValueError("Unknown stage dependency.")
                if predecessor.exited_at and visit.entered_at and predecessor.exited_at > visit.entered_at:
                    raise ValueError("A dependent stage starts before its predecessor exits.")
        remaining = set(visits)
        while remaining:
            ready = {identifier for identifier in remaining if not remaining.intersection(visits[identifier].predecessor_visit_ids)}
            if not ready:
                raise ValueError("Cyclic stage dependencies.")
            remaining -= ready
        return self


class AnalysisPolicy(Contract):
    version: Identifier = "synthetic-calendar-v1"
    time_convention: Literal["utc_calendar_hours_waiting_included"] = "utc_calendar_hours_waiting_included"
    end_to_end_sla_hours: Hours | None = 96
    stage_sla_hours: dict[str, Hours] = Field(default_factory=lambda: {
        "intake": 12, "review": 24, "approval": 48, "issuance": 12,
    })
    outlier_multiplier: float = Field(default=3, gt=0, allow_inf_nan=False)
    minimum_completed_cohort: int = Field(default=4, ge=4)


class SyntheticDataset(Contract):
    fixture_version: Identifier
    data_mode: Literal["synthetic"] = "synthetic"
    assumptions: tuple[str, ...]
    policy: AnalysisPolicy
    applications: tuple[Application, ...]

    @model_validator(mode="after")
    def unique_applications(self):
        for attribute in ("application_id", "workflow_instance_id"):
            identifiers = [getattr(item, attribute) for item in self.applications]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError(f"Duplicate {attribute}.")
        evidence_ids = [item.evidence_id for application in self.applications for item in application.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence identifiers must be unique across the dataset.")
        return self


class AnalysisRequest(Contract):
    as_of: Timestamp


class SummaryRequest(AnalysisRequest):
    received_month: str = Field(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")

    @model_validator(mode="after")
    def valid_month(self):
        datetime.strptime(self.received_month, "%Y-%m")
        return self


class Finding(Contract):
    finding_id: Identifier
    classification: Literal["observed", "reported", "hypothesis", "recommendation"]
    statement: str
    evidence_ids: tuple[Identifier, ...] = ()


class StageResult(Contract):
    visit_id: Identifier
    stage_code: str
    attempt: int
    elapsed_hours: Hours | None
    blocked_hours: Hours
    supported_blocked_hours: Hours


class StageTotal(Contract):
    stage_code: str
    elapsed_hours: Hours | None
    sla_hours: Hours | None
    sla_status: SlaStatus
    excess_hours: Hours | None


class ApplicationSummary(Contract):
    application_id: Identifier
    status: Literal["completed", "open"]
    elapsed_hours: Hours | None
    sla_status: SlaStatus
    sla_excess_hours: Hours | None
    outlier_status: Literal["outlier", "not_outlier", "insufficient_cohort", "not_eligible"]
    responsible_stage: str | None
    analysis_status: Literal["complete", "partial"]


class ApplicationAnalysis(ApplicationSummary):
    data_mode: Literal["synthetic"] = "synthetic"
    fixture_version: str
    policy_version: str
    as_of: Timestamp
    stage_visits: tuple[StageResult, ...]
    stage_totals: tuple[StageTotal, ...]
    summed_stage_hours: Hours | None
    stage_wall_clock_hours: Hours
    cause_status: Literal["documented_blocker", "insufficient_evidence"]
    findings: tuple[Finding, ...]
    hypotheses: tuple[Finding, ...] = ()
    recommendations: tuple[Finding, ...]
    evidence: tuple[Evidence, ...]
    data_quality_issues: tuple[str, ...]


class SummaryResponse(Contract):
    data_mode: Literal["synthetic"] = "synthetic"
    fixture_version: str
    policy_version: str
    received_month: str
    as_of: Timestamp
    application_count: int
    completed_count: int
    breached_count: int
    unknown_sla_count: int
    cohort_median_hours: Hours | None
    outlier_threshold_hours: Hours | None
    applications: tuple[ApplicationSummary, ...]
    warnings: tuple[str, ...]