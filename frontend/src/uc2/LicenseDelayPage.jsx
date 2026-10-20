import { useEffect, useState } from "react";
import { AlertCircle, ArrowUpRight, Database, FileSearch, LoaderCircle, RotateCw, ShieldCheck } from "lucide-react";
import { loadSyntheticDashboard } from "./api.js";
import { defaultFilters, DEMO_NOTICE, filterOptions, formatHours, formatTimestamp, stageTitle, syntheticPolicy } from "./syntheticData.js";
import LicenseSummaryCards from "./LicenseSummaryCards.jsx";
import LicenseApplicationsTable from "./LicenseApplicationsTable.jsx";
import WorkflowTimeline from "./WorkflowTimeline.jsx";
import EvidencePanel from "./EvidencePanel.jsx";
import "./uc2.css";

export function DashboardState({ status, onRetry }) {
  if (status === "loading") return <div className="uc2-state" role="status"><LoaderCircle className="uc2-spin" size={26} /><h2>Loading synthetic records</h2></div>;
  if (status === "error") return <div className="uc2-state" role="alert"><AlertCircle size={28} /><h2>Synthetic snapshot unavailable</h2><p>The local records could not be loaded.</p><button type="button" className="uc2-command" onClick={onRetry}><RotateCw size={15} />Retry</button></div>;
  return <div className="uc2-state" role="status"><FileSearch size={30} /><h2>No applications in this cohort</h2><p>No records match the received month and analysis cutoff.</p></div>;
}

export default function LicenseDelayPage({ loadDashboard = loadSyntheticDashboard }) {
  const [filters, setFilters] = useState(defaultFilters);
  const [result, setResult] = useState({ status: "loading", snapshot: null });
  const [selectedId, setSelectedId] = useState("SYN-D");
  const [selectedVisitId, setSelectedVisitId] = useState(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    Promise.resolve().then(() => loadDashboard(filters, { signal: controller.signal })).then((snapshot) => {
      if (controller.signal.aborted) return;
      setResult({ status: "ready", snapshot });
      setSelectedId((current) => snapshot.analyses[current] ? current : snapshot.summary.applications[0]?.application_id || null);
    }).catch(() => {
      if (!controller.signal.aborted) setResult({ status: "error", snapshot: null });
    });
    return () => controller.abort();
  }, [filters, attempt, loadDashboard]);
  const snapshot = result.snapshot;
  const analysis = snapshot?.analyses[selectedId];
  const workflow = snapshot?.workflows[selectedId];
  function selectApplication(identifier) {
    setSelectedId(identifier);
    setSelectedVisitId(null);
  }
  function changeFilters(nextFilters) {
    setResult({ status: "loading", snapshot: null });
    setSelectedVisitId(null);
    setFilters(nextFilters);
  }
  function retry() {
    setResult({ status: "loading", snapshot: null });
    setAttempt((value) => value + 1);
  }
  return <div className="uc2-dashboard">
    <a className="uc2-skip" href="#uc2-main">Skip to analysis</a>
    <header className="uc2-topbar"><div className="uc2-brand"><img src="/favicon.svg" width="32" height="32" alt="" /><div><strong>National Sports Platform</strong><span>License operations</span></div></div><span className="uc2-local-label"><Database size={14} aria-hidden="true" />Synthetic workspace</span></header>
    <div className="uc2-demo-notice" role="note"><ShieldCheck size={16} aria-hidden="true" /><strong>{DEMO_NOTICE}</strong></div>
    <main id="uc2-main" className="uc2-main">
      <div className="uc2-page-heading"><div><span className="uc2-eyebrow">UC2 / WORKFLOW ANALYTICS</span><h1>License processing</h1><p className="uc2-subtitle">Delay detection & evidence review</p></div><span className="uc2-policy"><ShieldCheck size={16} aria-hidden="true" /><span><strong>{syntheticPolicy.end_to_end_sla_hours}-hour demonstration SLA</strong><small>Synthetic assumption · UTC calendar hours, waiting included</small></span></span></div>
      <form className="uc2-filters" aria-label="Analysis filters" onSubmit={(event) => event.preventDefault()}>
        <label>Received month<select aria-label="Received month" value={filters.receivedMonth} onChange={(event) => changeFilters({ ...filters, receivedMonth: event.target.value })}>{filterOptions.months.map((month) => <option value={month} key={month}>{month === "2026-10" ? "October 2026" : "September 2026"}</option>)}</select></label>
        <label>Analysis cutoff (UTC)<select aria-label="Analysis cutoff (UTC)" value={filters.asOf} onChange={(event) => changeFilters({ ...filters, asOf: event.target.value })}>{filterOptions.cutoffs.map((cutoff) => <option key={cutoff} value={cutoff}>{formatTimestamp(cutoff)}</option>)}</select></label>
        <span className="uc2-snapshot-label"><span />{snapshot?.summary.fixture_version || "synthetic-workflows-v1"}</span>
      </form>
      {snapshot && <LicenseSummaryCards summary={snapshot.summary} presentation={snapshot.presentation} />}
      {result.status !== "ready" ? <DashboardState status={result.status} onRetry={retry} /> : !snapshot.summary.application_count ? <DashboardState status="empty" /> : <>
        <LicenseApplicationsTable summary={snapshot.summary} presentation={snapshot.presentation} selectedId={selectedId} onSelect={selectApplication} />
        {analysis && <section className="uc2-selected-application" aria-label={`Selected application ${selectedId}`}>
          <div className="uc2-selection-heading"><div><span className="uc2-eyebrow">APPLICATION REVIEW</span><h2>{selectedId}<ArrowUpRight size={18} aria-hidden="true" /></h2></div><dl className="uc2-application-metrics"><div><dt>Total duration</dt><dd>{formatHours(analysis.elapsed_hours)}</dd></div><div><dt>End-to-end SLA excess</dt><dd className={analysis.sla_status === "breached" ? "uc2-danger" : ""}>{formatHours(analysis.sla_excess_hours)}</dd></div><div><dt>Delayed stage attribution</dt><dd>{analysis.responsible_stage ? stageTitle(analysis.responsible_stage) : "Not established"}</dd></div></dl></div>
          <div className="uc2-detail-layout"><WorkflowTimeline analysis={analysis} workflow={workflow} policy={syntheticPolicy} selectedVisitId={selectedVisitId} onSelectVisit={setSelectedVisitId} /><EvidencePanel analysis={analysis} selectedVisitId={selectedVisitId} onClearVisit={() => setSelectedVisitId(null)} /></div>
        </section>}
      </>}
      <footer className="uc2-footer"><span>National Sports Platform · UC2</span><span>{snapshot?.summary.policy_version || syntheticPolicy.version} · Fictional records</span></footer>
    </main>
  </div>;
}