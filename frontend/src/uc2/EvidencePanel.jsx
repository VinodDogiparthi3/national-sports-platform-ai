import { AlertTriangle, FileText, X } from "lucide-react";
import { formatTimestamp } from "./syntheticData.js";

export default function EvidencePanel({ analysis, selectedVisitId, onClearVisit }) {
  const evidence = selectedVisitId ? analysis.evidence.filter((item) => item.visit_id === selectedVisitId) : analysis.evidence;
  const groups = [
    ["Observed facts", analysis.findings.filter((item) => item.classification === "observed")],
    ["Reported blockers", analysis.findings.filter((item) => item.classification === "reported")],
    ["Hypotheses", analysis.hypotheses],
    ["Recommended actions", analysis.recommendations],
  ];
  return <aside className="uc2-evidence" aria-labelledby="uc2-evidence-heading">
    <div className="uc2-section-heading"><div><h2 id="uc2-evidence-heading">Evidence & findings</h2><span>{analysis.application_id} · Synthetic workflow records</span></div><FileText size={18} aria-hidden="true" /></div>
    <div className="uc2-cause-notice"><AlertTriangle size={17} aria-hidden="true" /><p>{analysis.cause_status === "documented_blocker" ? "A blocker is documented. Its underlying root cause is not established." : "Insufficient evidence to establish an underlying root cause."}</p></div>
    {groups.map(([title, items]) => <section className="uc2-finding-group" key={title} aria-label={title}>
      <h3>{title}</h3>
      {items.length ? items.map((item) => <div className="uc2-finding" key={item.finding_id}><span className={`uc2-classification uc2-classification-${item.classification}`}>{item.classification}</span><p>{item.statement}</p>{item.evidence_ids.length > 0 && <div className="uc2-references">{item.evidence_ids.map((id) => <a href={`#uc2-evidence-${id}`} key={id} onClick={selectedVisitId ? onClearVisit : undefined}>{id}</a>)}</div>}</div>) : <p className="uc2-muted">{title === "Hypotheses" ? "None asserted." : "None recorded."}</p>}
    </section>)}
    <section className="uc2-finding-group" aria-label="Data quality"><h3>Missing evidence & data quality</h3>{analysis.data_quality_issues.length ? <ul>{analysis.data_quality_issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : <p className="uc2-muted">No validation issues in the supplied synthetic records.</p>}</section>
    <section className="uc2-records" aria-label="Evidence records"><div className="uc2-records-heading"><h3>Source evidence <span>({evidence.length})</span></h3>{selectedVisitId && <button type="button" onClick={onClearVisit} title="Show all evidence" aria-label="Show all evidence"><X size={16} /></button>}</div>
      {evidence.length ? evidence.map((item) => <details id={`uc2-evidence-${item.evidence_id}`} key={item.evidence_id}>
        <summary><FileText size={13} aria-hidden="true" /><span>{item.evidence_id}</span></summary>
        <dl><div><dt>Event time</dt><dd>{formatTimestamp(item.effective_at)}</dd></div><div><dt>Recorded</dt><dd>{formatTimestamp(item.recorded_at)}</dd></div></dl><p>{item.text}</p>
      </details>) : <p className="uc2-muted">No matching evidence records are available.</p>}
    </section>
  </aside>;
}