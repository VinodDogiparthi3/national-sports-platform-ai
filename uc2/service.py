from datetime import datetime
from statistics import median

from uc2.models import (
    AnalysisPolicy,
    AnalysisRequest,
    Application,
    ApplicationAnalysis,
    ApplicationSummary,
    Finding,
    StageResult,
    StageTotal,
    SummaryRequest,
    SummaryResponse,
)
from uc2.prompts import blocker_explanation, corrective_action
from uc2.repository import WorkflowRepository


def _hours(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600


def _union_hours(intervals: list[tuple[datetime, datetime]]) -> float:
    if not intervals:
        return 0
    ordered = sorted(intervals)
    start, end = ordered[0]
    total = 0.0
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += _hours(start, end)
            start, end = next_start, next_end
    return total + _hours(start, end)


def _sla(elapsed: float | None, limit: float | None, completed: bool):
    if elapsed is None or limit is None:
        return "unknown", None
    if elapsed > limit:
        return "breached", elapsed - limit
    return ("within_sla", 0) if completed else ("unknown", None)


def _cohort(applications: tuple[Application, ...], policy: AnalysisPolicy):
    durations = [
        _hours(item.received_at, item.completed_at)
        for item in applications if item.status == "completed" and item.completed_at is not None
    ]
    midpoint = float(median(durations)) if durations else None
    threshold = midpoint * policy.outlier_multiplier if midpoint is not None and len(durations) >= policy.minimum_completed_cohort else None
    return midpoint, threshold


def _analyze(application: Application, as_of: datetime, repository: WorkflowRepository, policy: AnalysisPolicy, threshold: float | None) -> ApplicationAnalysis:
    completed = application.status == "completed" and application.completed_at is not None
    endpoint = application.completed_at if completed else as_of
    elapsed = _hours(application.received_at, endpoint) if completed or application.status == "open" else None
    sla_status, sla_excess = _sla(elapsed, policy.end_to_end_sla_hours, completed)
    issues = []
    if application.status == "completed" and application.completed_at is None:
        issues.append("Completed application has no completion timestamp.")
    if not application.stage_visits:
        issues.append("No workflow stage visits are available.")
    evidence_by_id = {item.evidence_id: item for item in application.evidence}
    stage_results = []
    stage_intervals = {}
    stage_complete = {}
    all_intervals = []
    blocker_findings = []
    transition_ids = []
    for visit in application.stage_visits:
        end = visit.exited_at
        has_started_successor = any(
            visit.visit_id in item.predecessor_visit_ids and item.entered_at is not None
            for item in application.stage_visits
        )
        running = end is None and application.status == "open" and not has_started_successor
        if running:
            end = as_of
        duration = _hours(visit.entered_at, end) if visit.entered_at is not None and end is not None else None
        if visit.entered_at is None or (visit.exited_at is None and not running):
            issues.append(f"Visit {visit.visit_id} has missing stage timestamps.")
        visit_evidence = [evidence_by_id[identifier] for identifier in visit.evidence_ids]
        for label, timestamp in (("entry", visit.entered_at), ("exit", visit.exited_at)):
            if timestamp is not None:
                references = [item.evidence_id for item in visit_evidence if item.kind == "transition" and item.effective_at == timestamp]
                if not references:
                    issues.append(f"Visit {visit.visit_id} lacks matching {label} transition evidence.")
                transition_ids.extend(references)
        intervals = stage_intervals.setdefault(visit.stage_code, [])
        stage_complete[visit.stage_code] = stage_complete.get(visit.stage_code, True) and duration is not None and visit.exited_at is not None
        if duration is not None:
            interval = (visit.entered_at, end)
            intervals.append(interval)
            all_intervals.append(interval)
        blockers = []
        supported = []
        support_ids = []
        for blocker in visit.blocking_intervals:
            blocker_end = blocker.ended_at
            if blocker_end is None and running:
                blocker_end = as_of
            if visit.entered_at is None or blocker_end is None:
                issues.append(f"Visit {visit.visit_id} has an unmeasurable blocker interval.")
                continue
            blockers.append((blocker.started_at, blocker_end))
            records = [evidence_by_id[identifier] for identifier in blocker.evidence_ids]
            starts = [item for item in records if item.kind == "blocker_start" and item.effective_at == blocker.started_at]
            ends = [item for item in records if item.kind == "blocker_end" and item.effective_at == blocker.ended_at]
            if starts and (ends or blocker.ended_at is None):
                supported.append((blocker.started_at, blocker_end))
                support_ids.extend(item.evidence_id for item in starts + ends)
            else:
                issues.append(f"Visit {visit.visit_id} has a blocker without matching boundary evidence.")
        blocked_hours = _union_hours(blockers)
        supported_hours = _union_hours(supported)
        if supported_hours:
            blocker_findings.append(blocker_explanation(application.application_id, visit.visit_id, supported_hours, tuple(sorted(set(support_ids)))))
        stage_results.append(StageResult(
            visit_id=visit.visit_id, stage_code=visit.stage_code, attempt=visit.attempt,
            elapsed_hours=duration, blocked_hours=blocked_hours, supported_blocked_hours=supported_hours,
        ))
    totals = []
    for stage_code, intervals in stage_intervals.items():
        visits = [item for item in stage_results if item.stage_code == stage_code]
        stage_hours = _union_hours(intervals) if all(item.elapsed_hours is not None for item in visits) else None
        limit = policy.stage_sla_hours.get(stage_code)
        if limit is None:
            issues.append(f"Stage {stage_code} has no approved synthetic SLA threshold.")
        status, excess = _sla(stage_hours, limit, stage_complete[stage_code])
        totals.append(StageTotal(stage_code=stage_code, elapsed_hours=stage_hours, sla_hours=limit, sla_status=status, excess_hours=excess))
    wall_hours = _union_hours(all_intervals)
    sum_hours = sum(item.elapsed_hours for item in stage_results) if all(item.elapsed_hours is not None for item in stage_results) else None
    ordered_visits = sorted(application.stage_visits, key=lambda item: (item.entered_at or as_of, item.visit_id))
    sequential = bool(ordered_visits) and completed and not issues
    if sequential:
        sequential = ordered_visits[0].entered_at == application.received_at and ordered_visits[-1].exited_at == application.completed_at
        sequential = sequential and all(
            current.exited_at == following.entered_at and current.visit_id in following.predecessor_visit_ids
            for current, following in zip(ordered_visits, ordered_visits[1:])
        )
    if elapsed is not None and wall_hours != elapsed:
        issues.append("Workflow coverage is incomplete or contains unaccounted waiting time.")
    if sum_hours is not None and sum_hours > wall_hours:
        issues.append("Parallel visits overlap; sole-stage causal responsibility is not established.")
    if ordered_visits and completed and not sequential and not issues:
        issues.append("Workflow dependency linkage is incomplete; sole-stage responsibility is not established.")
    breached = [item.stage_code for item in totals if item.sla_status == "breached"]
    responsible = breached[0] if sequential and len(breached) == 1 and sla_status == "breached" else None
    findings = [Finding(
        finding_id=f"{application.application_id}-timing", classification="observed",
        statement=(f"Synthetic application elapsed time is {elapsed:g} hours; SLA status is {sla_status}." if elapsed is not None else "Insufficient evidence to calculate completed application elapsed time."),
        evidence_ids=tuple(sorted(set(transition_ids))),
    )]
    findings.extend(Finding(
        finding_id=f"{application.application_id}-{item.stage_code}-sla", classification="observed",
        statement=f"Stage {item.stage_code} exceeds its synthetic stage SLA by {item.excess_hours:g} hours; this is timing evidence, not proof of root cause.",
        evidence_ids=tuple(sorted({identifier for visit in application.stage_visits if visit.stage_code == item.stage_code for identifier in visit.evidence_ids})),
    ) for item in totals if item.sla_status == "breached")
    findings.extend(blocker_findings)
    cause_status = "documented_blocker" if responsible and any(
        item.stage_code == responsible and item.supported_blocked_hours > 0 for item in stage_results
    ) else "insufficient_evidence"
    if cause_status == "insufficient_evidence":
        findings.append(Finding(finding_id=f"{application.application_id}-insufficient-evidence", classification="observed", statement="Insufficient evidence to establish a root cause for an application delay."))
    recommendation_ids = tuple(sorted({identifier for finding in blocker_findings for identifier in finding.evidence_ids}))
    outlier = "not_eligible" if not completed else "insufficient_cohort" if threshold is None else "outlier" if elapsed > threshold else "not_outlier"
    return ApplicationAnalysis(
        application_id=application.application_id, status=application.status, elapsed_hours=elapsed,
        sla_status=sla_status, sla_excess_hours=sla_excess, outlier_status=outlier,
        responsible_stage=responsible, analysis_status="partial" if issues else "complete",
        fixture_version=repository.fixture_version, policy_version=policy.version, as_of=as_of,
        stage_visits=tuple(stage_results), stage_totals=tuple(totals), summed_stage_hours=sum_hours,
        stage_wall_clock_hours=wall_hours, cause_status=cause_status, findings=tuple(findings),
        recommendations=(corrective_action(application.application_id, recommendation_ids, cause_status == "documented_blocker"),) if sla_status == "breached" else (),
        evidence=application.evidence, data_quality_issues=tuple(issues),
    )


def summarize(request: SummaryRequest, repository: WorkflowRepository, policy: AnalysisPolicy | None = None) -> SummaryResponse:
    selected_policy = policy if policy is not None else repository.policy
    applications = repository.list_applications(request.received_month, request.as_of)
    midpoint, threshold = _cohort(applications, selected_policy)
    results = tuple(_analyze(item, request.as_of, repository, selected_policy, threshold) for item in applications)
    return SummaryResponse(
        fixture_version=repository.fixture_version, policy_version=selected_policy.version,
        received_month=request.received_month, as_of=request.as_of, application_count=len(results),
        completed_count=sum(item.status == "completed" and item.completed_at is not None for item in applications),
        breached_count=sum(item.sla_status == "breached" for item in results),
        unknown_sla_count=sum(item.sla_status == "unknown" for item in results),
        cohort_median_hours=midpoint, outlier_threshold_hours=threshold,
        applications=tuple(ApplicationSummary.model_validate(item.model_dump(include=set(ApplicationSummary.model_fields))) for item in results),
        warnings=repository.assumptions + ("The outlier rule is a small-sample demonstration heuristic, not a client-approved SLA.",),
    )


def analyze(application_id: str, as_of: datetime, repository: WorkflowRepository, policy: AnalysisPolicy | None = None) -> ApplicationAnalysis:
    cutoff = AnalysisRequest(as_of=as_of).as_of
    selected_policy = policy if policy is not None else repository.policy
    application = repository.get_application(application_id, cutoff)
    applications = repository.list_applications(application.received_at.strftime("%Y-%m"), cutoff)
    _, threshold = _cohort(applications, selected_policy)
    return _analyze(application, cutoff, repository, selected_policy, threshold)