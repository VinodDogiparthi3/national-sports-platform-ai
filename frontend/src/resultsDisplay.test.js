import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { parseResultCards, parseViewResults, splitResultsMarkdown } from "./resultsDisplay.js";

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

  it("shows separate equal cards for multiple metrics and keeps shared context", () => {
    const cards = parseResultCards([
      "| Age Group | Year | Saudi 2024 Final Total | Non-Saudi 2024 Total |",
      "| --- | --- | ---: | ---: |",
      "| 0–4 | 2024 | 2,126,538 | 378,651 |",
    ].join("\n"), "Population Estimates Statistics View");

    assert.deepEqual(cards.map(({ label, value, valueLabel, details }) => ({ label, value, valueLabel, details })), [
      { label: "Saudi Population (2024)", value: "2,126,538", valueLabel: "", details: ["Age Group: 0–4", "Year: 2024"] },
      { label: "Non-Saudi Population (2024)", value: "378,651", valueLabel: "", details: ["Age Group: 0–4", "Year: 2024"] },
    ]);
    assert.equal(cards[0].icon, cards[1].icon);
  });

  it("fans out every metric for every row without turning dimensions or IDs into metrics", () => {
    const cards = parseResultCards([
      "| Age Group | Year | Region ID | ContactID | RegionCode | Description | Saudi 2024 Final Total | Non-Saudi 2024 Total |",
      "| --- | ---: | --- | ---: | ---: | --- | ---: | ---: |",
      "| 0–4 | 2024 | 1001 | 90001 | 1001 | 2024 | 2,126,538 | 378,651 |",
      "| 5–9 | 2024 | 1002 | 90002 | 1002 | 2025 | 1,998,200 | 402,190 |",
    ].join("\n"), "Population Estimates Statistics View");

    assert.deepEqual(cards.map(({ label, value, details }) => [label, value, details]), [
      ["Saudi Population (2024)", "2,126,538", ["Age Group: 0–4", "Year: 2024", "Region ID: 1001", "ContactID: 90001", "RegionCode: 1001", "Description: 2024"]],
      ["Non-Saudi Population (2024)", "378,651", ["Age Group: 0–4", "Year: 2024", "Region ID: 1001", "ContactID: 90001", "RegionCode: 1001", "Description: 2024"]],
      ["Saudi Population (2024)", "1,998,200", ["Age Group: 5–9", "Year: 2024", "Region ID: 1002", "ContactID: 90002", "RegionCode: 1002", "Description: 2025"]],
      ["Non-Saudi Population (2024)", "402,190", ["Age Group: 5–9", "Year: 2024", "Region ID: 1002", "ContactID: 90002", "RegionCode: 1002", "Description: 2025"]],
    ]);
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

  it("keeps entity directories as tables instead of treating IDs and years as metrics", () => {
    const markdown = [
      "| Sport | Federation Membership Id | ContactID | Federation | Status | Registration Year |",
      "| --- | --- | --- | --- | --- | --- |",
      "| Table Tennis | Not specified | 123456 | Saudi Table Tennis Federation | Active | 2026 |",
    ].join("\n");
    assert.equal(parseResultCards(markdown, "Affiliated individuals"), null);
  });

  it("prefers the numeric metric over an earlier textual federation or ID column", () => {
    const cards = parseResultCards([
      "| Sport | Federation | ContactID | Registration Count |",
      "| --- | --- | --- | ---: |",
      "| Table Tennis | Saudi Table Tennis Federation | 123456 | 46 |",
    ].join("\n"));
    assert.equal(cards[0].value, "46");
    assert.equal(cards[0].valueLabel, "Registration Count");
    assert.deepEqual(cards[0].details, ["Federation: Saudi Table Tennis Federation", "ContactID: 123456"]);
  });

  it("preserves exact decimal precision and a valid zero", () => {
    const precise = parseResultCards("**Average Attendance Rate:** 0.5551325036207942");
    const zero = parseResultCards("**Age Groups:** 0");

    assert.equal(precise[0].value, "0.5551325036207942");
    assert.equal(zero[0].value, "0");
  });

  it("renders every returned grouped row", () => {
    const markdown = [
      "| Region | Indicator Count |",
      "| --- | --- |",
      ...Array.from({ length: 35 }, (_, index) => `| Region ${index} | ${index + 1} |`),
    ].join("\n");
    const cards = parseResultCards(markdown, "Health determinants by region");

    assert.equal(cards.length, 35);
    assert.equal(cards[0].label, "Region 0");
    assert.equal(cards[34].label, "Region 34");
    assert.equal(cards[34].value, "35");
  });

  it("supports Arabic values and sport names", () => {
    const cards = parseResultCards("- كرة القدم: ٢٥ لاعبًا", "إجمالي اللاعبين حسب الرياضة");
    assert.deepEqual(cards.map(({ icon, label }) => [icon, label]), [["⚽", "كرة القدم"]]);
  });

  it("returns null for ordinary prose", () => {
    assert.equal(parseResultCards("The data shows steady participation across all regions."), null);
  });
});

describe("parseViewResults", () => {
  it("keeps multiple views, source details, and numeric values separate", () => {
    const views = parseViewResults([
      {
        view_id: "budget_view_01",
        display_name: "Budget View",
        markdown: "### Results\n\n| Project | Budget Total |\n| --- | --- |\n| Community Sports | 1250000 |",
        source: { dataset: "dw_sharepoint_views", object_name: "budget_view_01" },
      },
      {
        view_id: "budget_control_view_01",
        display_name: "Budget Control View",
        markdown: "### Results\n\n| Agency | Operating Expenditure |\n| --- | --- |\n| Sports Authority | 415000 |",
        source: { dataset: "dw_sharepoint_views", object_name: "budget_control_view_01" },
      },
    ]);

    assert.deepEqual(views.map(({ display_name }) => display_name), ["Budget View", "Budget Control View"]);
    assert.deepEqual(views.map(({ cards }) => [cards[0].label, cards[0].value]), [
      ["Community Sports", "1250000"],
      ["Sports Authority", "415000"],
    ]);
    assert.deepEqual(views.map(({ source }) => source.object_name), ["budget_view_01", "budget_control_view_01"]);
  });
});