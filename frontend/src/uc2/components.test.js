import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { defaultFilters, selectSyntheticSnapshot, syntheticPolicy } from "./syntheticData.js";

let server;
let components;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL("../../", import.meta.url)), server: { middlewareMode: true, hmr: false, watch: null }, optimizeDeps: { noDiscovery: true } });
  components = await Promise.all(["LicenseSummaryCards", "LicenseApplicationsTable", "WorkflowTimeline", "EvidencePanel"].map(async (name) => (await server.ssrLoadModule(`/src/uc2/${name}.jsx`)).default));
});
after(async () => { await server?.close(); });

describe("UC2 component contracts", () => {
  it("renders KPIs and accessible selectable applications", () => {
    const snapshot = selectSyntheticSnapshot(defaultFilters);
    const cards = renderToStaticMarkup(React.createElement(components[0], snapshot));
    assert.match(cards, /Applications received/);
    assert.match(cards, /66 h/);
    const table = renderToStaticMarkup(React.createElement(components[1], { ...snapshot, selectedId: "SYN-D", onSelect() {} }));
    assert.match(table, /Inspect SYN-D/);
    assert.match(table, /aria-pressed="true"/);
    assert.match(table, /600 h/);
    assert.match(table, /SLA breached/);
    assert.match(table, /Outlier/);
    assert.match(table, /scope="col"/);
  });

  it("renders exact timeline measurements and raw timestamp records", () => {
    const snapshot = selectSyntheticSnapshot(defaultFilters);
    const html = renderToStaticMarkup(React.createElement(components[2], { analysis: snapshot.analyses["SYN-D"], workflow: snapshot.workflows["SYN-D"], policy: syntheticPolicy, selectedVisitId: "D-A", onSelectVisit() {} }));
    for (const value of ["Intake", "Review", "Approval", "Issuance", "564 h", "516 h", "552 h", "02 Oct 2026", "25 Oct 2026"]) assert.ok(html.includes(value), value);
    assert.match(html, /Inspect Approval evidence/);
    assert.match(html, /SYN-D-VERIFY-BLOCK-START/);
  });

  it("separates observed facts, reported blockers, hypotheses and recommendations", () => {
    const snapshot = selectSyntheticSnapshot(defaultFilters);
    const html = renderToStaticMarkup(React.createElement(components[3], { analysis: snapshot.analyses["SYN-D"], onClearVisit() {} }));
    for (const text of ["Observed facts", "Reported blockers", "Hypotheses", "None asserted.", "Recommended actions", "SYN-D-VERIFY-BLOCK-END", "root cause is not established"]) assert.ok(html.includes(text), text);
    assert.match(html, /classification-recommendation/);
  });

  it("renders missing timestamps, partial evidence and insufficient-evidence warnings", () => {
    const snapshot = selectSyntheticSnapshot({ ...defaultFilters, asOf: "2026-10-03T09:00:00Z" });
    const props = { analysis: snapshot.analyses["SYN-D"], workflow: snapshot.workflows["SYN-D"], policy: syntheticPolicy, onSelectVisit() {}, onClearVisit() {} };
    const timeline = renderToStaticMarkup(React.createElement(components[2], props));
    assert.match(timeline, /Not recorded/);
    const evidence = renderToStaticMarkup(React.createElement(components[3], { ...props, selectedVisitId: "unknown" }));
    assert.match(evidence, /Insufficient evidence/);
    assert.match(evidence, /No matching evidence records/);
    const empty = renderToStaticMarkup(React.createElement(components[2], { ...props, analysis: { ...props.analysis, stage_visits: [] } }));
    assert.match(empty, /Workflow stage records are unavailable/);
  });

  it("renders the synthetic banner and accessible loading, error and empty states", async () => {
    const page = await server.ssrLoadModule("/src/uc2/LicenseDelayPage.jsx");
    const html = renderToStaticMarkup(React.createElement(page.default));
    assert.match(html, /SYNTHETIC DEMO DATA/);
    assert.match(html, /NOT REAL LICENSE RECORDS/);
    assert.match(html, /96-hour demonstration SLA/);
    assert.match(html, /aria-label="Analysis cutoff \(UTC\)"/);
    assert.match(html, /role="status"/);
    const error = renderToStaticMarkup(React.createElement(page.DashboardState, { status: "error", onRetry() {} }));
    assert.match(error, /role="alert"/);
    assert.match(error, /Retry/);
    const empty = renderToStaticMarkup(React.createElement(page.DashboardState, { status: "empty" }));
    assert.match(empty, /No applications in this cohort/);
  });
});