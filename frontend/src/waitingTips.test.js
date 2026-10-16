import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { nextTipIndex, stepState, waitingTips } from "./waitingTips.js";

describe("waitingTips", () => {
  it("offers the same number of tips in English and Arabic", () => {
    assert.ok(waitingTips.en.length > 0);
    assert.equal(waitingTips.en.length, waitingTips.ar.length);
  });

  it("has no empty tips", () => {
    for (const tip of [...waitingTips.en, ...waitingTips.ar]) assert.ok(tip.trim().length > 10);
  });
});

describe("stepState", () => {
  it("marks earlier steps done, the current one active, and later ones pending", () => {
    assert.deepEqual([0, 1, 2].map((step) => stepState(step, 1)), ["done", "active", "pending"]);
    assert.deepEqual([0, 1, 2].map((step) => stepState(step, 0)), ["active", "pending", "pending"]);
    assert.deepEqual([0, 1, 2].map((step) => stepState(step, 2)), ["done", "done", "active"]);
  });
});

describe("nextTipIndex", () => {
  it("wraps around and tolerates an empty list", () => {
    assert.equal(nextTipIndex(0, 4), 1);
    assert.equal(nextTipIndex(3, 4), 0);
    assert.equal(nextTipIndex(0, 0), 0);
  });
});
