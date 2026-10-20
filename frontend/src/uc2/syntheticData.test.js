import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { loadSyntheticDashboard } from "./api.js";
import { defaultFilters, DEMO_NOTICE, filterOptions, formatHours, formatTimestamp, selectSyntheticSnapshot, syntheticPolicy } from "./syntheticData.js";

describe("UC2 bundled synthetic adapter", () => {
  it("preserves Python-generated summary counts, durations, SLA and outlier results", () => {
    const { summary, presentation } = selectSyntheticSnapshot(defaultFilters);
    assert.equal(summary.data_mode, "synthetic");
    assert.deepEqual([summary.application_count, summary.completed_count, summary.breached_count, presentation.outlier_count], [4, 4, 1, 1]);
    assert.deepEqual(summary.applications.map((item) => item.elapsed_hours), [48, 72, 60, 600]);
    assert.equal(summary.cohort_median_hours, 66);
    assert.equal(summary.outlier_threshold_hours, 198);
    assert.deepEqual(summary.applications.map((item) => item.sla_status), ["within_sla", "within_sla", "within_sla", "breached"]);
    assert.equal(syntheticPolicy.end_to_end_sla_hours, 96);
    assert.match(DEMO_NOTICE, /NOT REAL LICENSE RECORDS/);
  });

  it("keeps stage excess, end-to-end excess and blocker durations distinct", () => {
    const { analyses, workflows } = selectSyntheticSnapshot(defaultFilters);
    const delayed = analyses["SYN-D"];
    assert.equal(delayed.sla_excess_hours, 504);
    assert.deepEqual([delayed.stage_totals[2].elapsed_hours, delayed.stage_totals[2].excess_hours, delayed.stage_visits[2].supported_blocked_hours], [564, 516, 552]);
    assert.equal(workflows["SYN-D"].stage_visits[2].entered_at, "2026-10-02T09:00:00Z");
    assert.equal(workflows["SYN-D"].stage_visits[2].exited_at, "2026-10-25T21:00:00Z");
  });

  it("preserves valid evidence links and does not invent hypotheses", () => {
    for (const analysis of Object.values(selectSyntheticSnapshot(defaultFilters).analyses)) {
      const ids = new Set(analysis.evidence.map((item) => item.evidence_id));
      assert.deepEqual(analysis.hypotheses, []);
      for (const finding of [...analysis.findings, ...analysis.recommendations]) {
        assert.ok(finding.evidence_ids.every((id) => ids.has(id)));
      }
      assert.ok(analysis.evidence.every((item) => item.synthetic));
    }
  });

  it("selects the precomputed empty month without performing analytics", () => {
    const snapshot = selectSyntheticSnapshot({ ...defaultFilters, receivedMonth: "2026-09" });
    assert.deepEqual(snapshot.summary.applications, []);
    assert.equal(snapshot.summary.application_count, 0);
    assert.equal(snapshot.summary.cohort_median_hours, null);
  });

  it("uses cutoff-specific workflow snapshots without leaking future evidence", () => {
    const snapshot = selectSyntheticSnapshot({ ...defaultFilters, asOf: "2026-10-03T09:00:00Z" });
    assert.equal(snapshot.analyses["SYN-D"].status, "open");
    assert.equal(snapshot.analyses["SYN-D"].cause_status, "insufficient_evidence");
    assert.equal(snapshot.workflows["SYN-D"].stage_visits[2].exited_at, null);
    assert.ok(!snapshot.analyses["SYN-D"].evidence.some((item) => item.evidence_id === "SYN-D-VERIFY-BLOCK-END"));
    assert.equal(filterOptions.cutoffs.length, 3);
  });

  it("returns independent copies and rejects unsupported snapshots", () => {
    const first = selectSyntheticSnapshot(defaultFilters);
    first.summary.application_count = 999;
    assert.equal(selectSyntheticSnapshot(defaultFilters).summary.application_count, 4);
    assert.throws(() => selectSyntheticSnapshot({ ...defaultFilters, receivedMonth: "2030-01" }), /unavailable/);
  });

  it("loads without fetch or external APIs and supports cancellation", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = () => { throw new Error("Network forbidden"); };
    try {
      assert.equal((await loadSyntheticDashboard(defaultFilters)).summary.application_count, 4);
      const controller = new AbortController();
      controller.abort();
      await assert.rejects(loadSyntheticDashboard(defaultFilters, { signal: controller.signal }), { name: "AbortError" });
    } finally {
      globalThis.fetch = original;
    }
  });

  it("formats missing values and timezone labels without manufacturing durations", () => {
    assert.equal(formatHours(null), "Not recorded");
    assert.equal(formatHours(0), "0 h");
    assert.match(formatTimestamp("2026-10-01T09:00:00Z"), /09:00 UTC$/);
    assert.equal(formatTimestamp(null), "Not recorded");
  });
});