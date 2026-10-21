import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let SqlPreview;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false, watch: null }, optimizeDeps: { noDiscovery: true } });
  SqlPreview = (await server.ssrLoadModule("/src/SqlPreview.jsx")).default;
});
after(async () => server?.close());

describe("SQL preview presentation", () => {
  const preview = {
    status: "PREVIEW_ONLY",
    notice: "No query executed; no results available.",
    authorized_source_ids: ["nsp_base_view_01"],
    schema_backup: { identity: "sha256:manifest", created_at: "2026-10-20T12:00:00Z" },
    queries: [{ source_id: "nsp_base_view_01", sql: "SELECT sport FROM approved_source" }],
  };

  it("displays the explicit preview-only contract without rendering a results view", () => {
    const html = renderToStaticMarkup(React.createElement(SqlPreview, { preview }));
    assert.match(html, /PREVIEW_ONLY/);
    assert.match(html, /No query executed; no results available/);
    assert.match(html, /sha256:manifest/);
    assert.match(html, /2026-10-20T12:00:00Z/);
    assert.match(html, /SELECT sport FROM approved_source/);
    assert.doesNotMatch(html, /result-card|view-result|chart/i);
  });

  it("localizes preview labels while keeping SQL left-to-right", () => {
    const html = renderToStaticMarkup(React.createElement(SqlPreview, { preview, language: "ar" }));
    assert.match(html, /معاينة SQL/);
    assert.match(html, /dir="ltr"/);
  });
});