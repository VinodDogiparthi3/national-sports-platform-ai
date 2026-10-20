import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { getUseCase, navigateUseCase, buildUseCaseUrl } from "./navigation.js";

describe("local platform navigation", () => {
  it("defaults to UC1 and recognizes UC2 query strings on refresh", () => {
    for (const query of ["", "?language=en", "?usecase=uc1", "?usecase=unknown"]) assert.equal(getUseCase(query), "uc1");
    assert.equal(getUseCase("?language=en&usecase=uc2"), "uc2");
  });

  it("preserves unrelated query parameters and paths when switching", () => {
    assert.equal(buildUseCaseUrl("http://localhost:5173/?language=ar&filter=one&filter=two", "uc2"), "/?language=ar&filter=one&filter=two&usecase=uc2");
    assert.equal(buildUseCaseUrl("http://localhost:5173/?language=ar&usecase=uc2#uc2-evidence-D-I-IN", "uc1"), "/?language=ar");
    assert.throws(() => buildUseCaseUrl("http://localhost:5173/", "other"), /Unknown/);
  });

  it("does not consume evidence fragments as use-case routes", () => {
    const url = new URL("http://localhost:5173/?usecase=uc2#uc2-evidence-D-I-IN");
    assert.equal(getUseCase(url.search), "uc2");
    const history = { state: { existing: true }, pushState() { throw new Error("Unexpected navigation"); } };
    assert.equal(navigateUseCase(history, url, "uc2"), false);
    assert.equal(url.hash, "#uc2-evidence-D-I-IN");
  });

  it("uses a normal history entry and preserves existing history state", () => {
    const calls = [];
    const history = { state: { existing: "state" }, pushState(...args) { calls.push(args); } };
    assert.equal(navigateUseCase(history, new URL("http://localhost:5173/?language=en"), "uc2"), true);
    assert.deepEqual(calls, [[history.state, "", "/?language=en&usecase=uc2"]]);
  });
});