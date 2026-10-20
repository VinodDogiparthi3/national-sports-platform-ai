import bundle from "./syntheticResponses.json" with { type: "json" };

export const DEMO_NOTICE = "SYNTHETIC DEMO DATA — NOT REAL LICENSE RECORDS";
export const filterOptions = Object.freeze({ months: bundle.months, cutoffs: bundle.cutoffs });
export const syntheticPolicy = Object.freeze(bundle.policy);
export const defaultFilters = Object.freeze({ receivedMonth: bundle.months[0], asOf: bundle.cutoffs[0] });

export function selectSyntheticSnapshot({ receivedMonth, asOf }) {
  const snapshot = bundle.snapshots.find((item) => item.received_month === receivedMonth && item.as_of === asOf);
  if (!snapshot || bundle.data_mode !== "synthetic" || snapshot.summary.data_mode !== "synthetic") {
    throw new Error("The requested synthetic snapshot is unavailable.");
  }
  return structuredClone(snapshot);
}

export function formatHours(value) {
  return value == null ? "Not recorded" : `${new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 }).format(value)} h`;
}

export function formatTimestamp(value) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not recorded";
  return `${new Intl.DateTimeFormat("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", hour12: false,
  }).format(date)} UTC`;
}

export function stageTitle(code) {
  return { intake: "Intake", review: "Review", approval: "Approval", issuance: "Issuance" }[code] || code;
}

export function statusLabel(status) {
  return {
    within_sla: "Within SLA", breached: "SLA breached", unknown: "SLA unknown",
    outlier: "Outlier", not_outlier: "Not an outlier", insufficient_cohort: "Insufficient cohort", not_eligible: "Not eligible",
  }[status] || status;
}