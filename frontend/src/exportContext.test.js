import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { streamQuestion } from "./api.js";
import { getLatestExportContext, resolveDownloadUrl } from "./exportContext.js";

const originalFetch = globalThis.fetch;

function sseResponse(events) {
  const text = events
    .map(([event, payload]) => `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`)
    .join("");
  const encoder = new TextEncoder();
  return {
    ok: true,
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(text));
        controller.close();
      },
    }),
  };
}

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("streamQuestion export support", () => {
  it("sends the conversation's export context with the question", async () => {
    let request;
    globalThis.fetch = async (url, options) => {
      request = { url, body: JSON.parse(options.body) };
      return sseResponse([["done", { question: "q" }]]);
    };

    await streamQuestion("Export these results to Excel.", "en", () => {}, "ctx-token");

    assert.ok(request.url.endsWith("/ask/stream"));
    assert.deepEqual(request.body, {
      question: "Export these results to Excel.",
      language: "en",
      export_context: "ctx-token",
    });
  });

  it("sends a null context when the conversation has no results yet", async () => {
    let body;
    globalThis.fetch = async (_url, options) => {
      body = JSON.parse(options.body);
      return sseResponse([["done", {}]]);
    };

    await streamQuestion("hello", "ar", () => {});

    assert.equal(body.export_context, null);
    assert.equal(body.language, "ar");
  });

  it("delivers export and context events to the caller", async () => {
    globalThis.fetch = async () =>
      sseResponse([
        ["status", { step: "understanding" }],
        ["token", { text: "Your Excel report is ready." }],
        ["export", { download_url: "/exports/abc", row_count: 3 }],
        ["context", { export_context: "new-ctx" }],
        ["done", { question: "q" }],
      ]);

    const received = [];
    const completion = await streamQuestion("q", "en", (event, payload) => received.push([event, payload]));

    assert.deepEqual(completion, { question: "q" });
    assert.deepEqual(
      received.find(([event]) => event === "export")[1],
      { download_url: "/exports/abc", row_count: 3 },
    );
    assert.deepEqual(received.find(([event]) => event === "context")[1], { export_context: "new-ctx" });
  });
});

describe("export context selection", () => {
  const assistant = (id, exportContext) => ({ id, role: "assistant", text: "", ...(exportContext ? { exportContext } : {}) });

  it("uses the most recent results in the conversation", () => {
    const messages = [
      { id: "1", role: "user", text: "q1" },
      assistant("2", "first"),
      { id: "3", role: "user", text: "q2" },
      assistant("4", "second"),
      { id: "5", role: "user", text: "Export these results to Excel." },
    ];
    assert.equal(getLatestExportContext(messages), "second");
  });

  it("still finds earlier results after an export or clarification reply", () => {
    const messages = [assistant("1", "results"), assistant("2"), assistant("3")];
    assert.equal(getLatestExportContext(messages), "results");
  });

  it("returns null for a conversation without results so the API asks which data to export", () => {
    assert.equal(getLatestExportContext([]), null);
    assert.equal(getLatestExportContext([{ id: "1", role: "user", text: "hi", exportContext: "ignored" }]), null);
  });

  it("keeps each conversation's results separate", () => {
    const conversationA = [assistant("a1", "context-a")];
    const conversationB = [assistant("b1", "context-b")];
    assert.equal(getLatestExportContext(conversationA), "context-a");
    assert.equal(getLatestExportContext(conversationB), "context-b");
  });

  it("builds an absolute download link from the API base URL", () => {
    assert.equal(resolveDownloadUrl("http://127.0.0.1:8000", "/exports/abc"), "http://127.0.0.1:8000/exports/abc");
    assert.equal(resolveDownloadUrl("", "/exports/abc"), "/exports/abc");
  });
});
