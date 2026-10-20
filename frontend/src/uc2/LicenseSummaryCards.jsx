import { CircleCheck, Clock3, FileStack, ShieldAlert, TrendingUp } from "lucide-react";
import { formatHours } from "./syntheticData.js";

export default function LicenseSummaryCards({ summary, presentation }) {
  const metrics = [
    ["Applications received", summary.application_count, FileStack, "neutral"],
    ["Completed", summary.completed_count, CircleCheck, "green"],
    ["SLA breaches", summary.breached_count, ShieldAlert, "red"],
    ["Processing outliers", presentation.outlier_count, TrendingUp, "amber"],
    ["Median processing", formatHours(summary.cohort_median_hours), Clock3, "blue"],
  ];
  return <section className="uc2-kpis" aria-label="License processing summary">
    {metrics.map(([label, value, Icon, tone]) => <article className={`uc2-kpi uc2-tone-${tone}`} key={label}>
      <div><span>{label}</span><Icon size={17} aria-hidden="true" /></div>
      <strong>{value}</strong>
    </article>)}
  </section>;
}