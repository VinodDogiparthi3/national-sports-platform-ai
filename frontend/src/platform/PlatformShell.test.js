import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let PlatformShell;
const previousWindow = globalThis.window;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL("../../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false, watch: null }, optimizeDeps: { noDiscovery: true } });
  PlatformShell = (await server.ssrLoadModule("/src/platform/PlatformShell.jsx")).default;
});
after(async () => {
  if (previousWindow === undefined) delete globalThis.window;
  else globalThis.window = previousWindow;
  await server?.close();
});

function markup(query = "") {
  globalThis.window = { location: new URL(`http://localhost:5173/${query}`), matchMedia: () => ({ matches: false }) };
  return renderToStaticMarkup(React.createElement(PlatformShell));
}

describe("local platform view boundaries", () => {
  it("keeps UC1 as the default view and leaves UC2 unmounted until visited", () => {
    const html = markup();
    assert.match(html, /aria-current="page"[^>]*><svg[^]*?Conversational AI/);
    assert.match(html, /data-usecase="uc1"[^>]*aria-hidden="false"/);
    assert.match(html, /data-usecase="uc2"[^>]*hidden=""[^>]*inert=""/);
    assert.ok(!html.includes("Loading synthetic dashboard"));
  });

  it("preserves the UC1 subtree when a refreshed URL selects UC2", () => {
    const html = markup("?language=ar&usecase=uc2#uc2-evidence-D-I-IN");
    assert.match(html, /data-usecase="uc1"[^>]*hidden=""[^>]*inert=""/);
    assert.match(html, /class="assistant-app/);
    assert.match(html, /data-usecase="uc2"[^>]*lang="en"[^>]*dir="ltr"/);
    assert.match(html, /Loading synthetic dashboard/);
    assert.match(html, /language=ar/);
  });

  it("provides normal accessible links with no hidden-view navigation targets", () => {
    const html = markup();
    assert.match(html, /<nav aria-label="Use cases"/);
    assert.match(html, /href="\/\?usecase=uc2"/);
    assert.match(html, /License Delay Analysis/);
    assert.match(html, /Synthetic Demo/);
    assert.match(html, /role="region" aria-label="Conversational AI"/);
  });
});