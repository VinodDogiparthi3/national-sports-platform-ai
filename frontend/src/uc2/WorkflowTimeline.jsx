import { Check, ChevronRight, Clock3, LockKeyhole } from "lucide-react";
import { formatHours, formatTimestamp, stageTitle } from "./syntheticData.js";

export default function WorkflowTimeline({ analysis, workflow, policy, selectedVisitId, onSelectVisit }) {
  if (!analysis.stage_visits.length) return <p role="status" className="uc2-empty">Workflow stage records are unavailable.</p>;
  return <section className="uc2-timeline" aria-labelledby="uc2-workflow-heading">
    <div className="uc2-section-heading"><div><h2 id="uc2-workflow-heading">Processing workflow</h2><span>{analysis.application_id} · {analysis.analysis_status === "partial" ? "Partial evidence" : "Recorded stage sequence"}</span></div><span className="uc2-count">{analysis.stage_visits.length} visits</span></div>
    <ol className="uc2-stage-list">{analysis.stage_visits.map((visit, index) => {
      const record = workflow?.stage_visits.find((item) => item.visit_id === visit.visit_id);
      const total = analysis.stage_totals.find((item) => item.stage_code === visit.stage_code);
      const breached = total?.sla_status === "breached";
      const selected = visit.visit_id === selectedVisitId;
      const Icon = record?.exited_at ? Check : Clock3;
      return <li className={`${breached ? "uc2-stage-breached" : ""} ${selected ? "uc2-stage-selected" : ""}`} key={visit.visit_id}>
        <div className="uc2-stage-marker" aria-hidden="true"><Icon size={15} /></div>
        <div className="uc2-stage-content">
          <button type="button" className="uc2-stage-button" aria-label={`Inspect ${stageTitle(visit.stage_code)} evidence`} aria-pressed={selected} onClick={() => onSelectVisit(selected ? null : visit.visit_id)}>
            <span><span className="uc2-stage-number">{String(index + 1).padStart(2, "0")}</span><strong>{stageTitle(visit.stage_code)}</strong>{visit.attempt > 1 && <small>Attempt {visit.attempt}</small>}</span>
            <span><strong>{formatHours(visit.elapsed_hours)}</strong><ChevronRight size={15} aria-hidden="true" /></span>
          </button>
          <dl className="uc2-stage-meta">
            <div><dt>Entry</dt><dd>{formatTimestamp(record?.entered_at)}</dd></div>
            <div><dt>Exit</dt><dd>{formatTimestamp(record?.exited_at)}</dd></div>
            <div><dt>Stage SLA</dt><dd>{formatHours(total?.sla_hours ?? policy.stage_sla_hours[visit.stage_code])}</dd></div>
            <div><dt>Stage excess</dt><dd className={breached ? "uc2-danger" : ""}>{formatHours(total?.excess_hours)}</dd></div>
          </dl>
          {visit.supported_blocked_hours > 0 && <div className="uc2-blocker"><LockKeyhole size={14} aria-hidden="true" /><strong>{formatHours(visit.supported_blocked_hours)} documented blocker</strong><span>Recorded waiting, not a confirmed underlying root cause</span></div>}
          {record?.blocking_intervals.map((blocker, blockerIndex) => <p className="uc2-blocker-interval" key={`${visit.visit_id}-${blockerIndex}`}>Blocker interval: {formatTimestamp(blocker.started_at)} to {formatTimestamp(blocker.ended_at)}<br /><span>{blocker.evidence_ids.join(" · ") || "No linked boundary evidence"}</span></p>)}
        </div>
      </li>;
    })}</ol>
  </section>;
}