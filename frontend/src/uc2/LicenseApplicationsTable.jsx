import { ArrowRight, CircleAlert, CircleCheck } from "lucide-react";
import { formatHours, statusLabel } from "./syntheticData.js";

export default function LicenseApplicationsTable({ summary, presentation, selectedId, onSelect }) {
  return <section className="uc2-applications" aria-labelledby="uc2-applications-heading">
    <div className="uc2-section-heading"><div><h2 id="uc2-applications-heading">Application comparison</h2><span>{summary.application_count} applications in the received cohort</span></div><span className="uc2-note">Duration in calendar hours</span></div>
    <div className="uc2-table-scroll" role="region" aria-label="Application comparison table" tabIndex={0}>
      <table><caption className="uc2-sr-only">Synthetic application processing durations, SLA and outlier results</caption>
        <thead><tr><th scope="col">Application</th><th scope="col">Processing time</th><th scope="col">SLA status</th><th scope="col">Outlier status</th><th scope="col">Workflow</th></tr></thead>
        <tbody>{summary.applications.map((application) => {
          const breached = application.sla_status === "breached";
          const Icon = breached ? CircleAlert : CircleCheck;
          const width = presentation.maximum_elapsed_hours ? ((application.elapsed_hours || 0) / presentation.maximum_elapsed_hours) * 100 : 0;
          return <tr key={application.application_id} className={selectedId === application.application_id ? "uc2-selected-row" : ""}>
            <th scope="row"><button type="button" className="uc2-application-button" aria-label={`Inspect ${application.application_id}`} aria-pressed={selectedId === application.application_id} onClick={() => onSelect(application.application_id)}>{application.application_id}<ArrowRight size={15} aria-hidden="true" /></button></th>
            <td><div className="uc2-duration"><strong>{formatHours(application.elapsed_hours)}</strong><span className={`uc2-duration-track ${breached ? "is-breached" : ""}`} aria-hidden="true"><span style={{ width: `${width}%` }} /></span></div></td>
            <td><span className={`uc2-status ${breached ? "uc2-status-red" : application.sla_status === "within_sla" ? "uc2-status-green" : "uc2-status-neutral"}`}><Icon size={13} aria-hidden="true" />{statusLabel(application.sla_status)}</span></td>
            <td><span className={application.outlier_status === "outlier" ? "uc2-outlier" : "uc2-muted"}>{statusLabel(application.outlier_status)}</span></td>
            <td><span className="uc2-workflow-state">{application.status === "completed" ? "Completed" : "In progress"}</span></td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  </section>;
}