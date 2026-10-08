import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { parseResultCards, splitResultsMarkdown } from "./resultsDisplay.js";

describe("splitResultsMarkdown", () => {
  it("keeps the heading visible and separates a standalone total", () => {
    assert.deepEqual(
      splitResultsMarkdown("### Total Athletes\n\n**Total Athletes:** 12,450"),
      { summary: "### Total Athletes", results: "**Total Athletes:** 12,450" },
    );
  });

  it("separates grouped lists and markdown tables", () => {
    assert.equal(splitResultsMarkdown("### By Sport\n\n- Football: 12").summary, "### By Sport");
    assert.equal(splitResultsMarkdown("### By Sport\n\n| Sport | Total |\n| --- | ---: |\n| Football | 12 |").summary, "### By Sport");
  });
});

describe("parseResultCards", () => {
  it("turns categorized sports bullets into compact icon and value rows", () => {
    const cards = parseResultCards(
      "- Botshia · **1 Federation**\n- Netball · **1 Federation**\n- Aikido · **1 Federation**",
      "Total Federations by Sport",
    );

    assert.deepEqual(cards.map(({ icon, label, value }) => [icon, label, value]), [
      ["🏛️", "Botshia", "1 Federation"],
      ["🏐", "Netball", "1 Federation"],
      ["🥋", "Aikido", "1 Federation"],
    ]);
  });

  it("uses athlete and ranking context for non-sport names", () => {
    const cards = parseResultCards("1. Athlete One — 25 registrations", "Top 5 Athletes by Registrations");
    assert.deepEqual(cards[0], {
      icon: "🏆",
      label: "Athlete One",
      value: "25 registrations",
      valueLabel: "",
      details: [],
    });
  });

  it("highlights table metrics and preserves remaining columns", () => {
    const cards = parseResultCards(
      "| Sport | Registrations | Region |\n| --- | ---: | --- |\n| Football | 2,345 | Central |",
      "Top sports by registrations",
    );

    assert.deepEqual(cards[0], {
      icon: "⚽",
      label: "Football",
      value: "2,345",
      valueLabel: "Registrations",
      details: ["Region: Central"],
    });
  });

  it("renders a standalone athlete total as a people metric", () => {
    const cards = parseResultCards("**Total Athletes:** 12,450", "### Total Athletes");
    assert.deepEqual(cards[0], {
      icon: "👥",
      label: "Total Athletes",
      value: "12,450",
      valueLabel: "",
      details: [],
    });
  });

  it("supports Arabic values and sport names", () => {
    const cards = parseResultCards("- كرة القدم: ٢٥ لاعبًا", "إجمالي اللاعبين حسب الرياضة");
    assert.deepEqual(cards.map(({ icon, label }) => [icon, label]), [["⚽", "كرة القدم"]]);
  });

  it("returns null for ordinary prose", () => {
    assert.equal(parseResultCards("The data shows steady participation across all regions."), null);
  });
});