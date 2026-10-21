import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { streamQuestion } from "./api.js";
import {
  getConversationHistory,
  getLatestConversationContext,
  getLatestExportContext,
  mergeSources,
  resolveDownloadUrl,
} from "./exportContext.js";

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

describe("streamQuestion conversation context", () => {
  it("returns the three-region clarification context on all in the same conversation", async () => {
    const question = "total no.of regions and list of regions";
    const sourceIds = ["dw_ms_crm_onprem.ntw_regionBase", "population_estimates_statistics_view_01", "nsp_base_view_01"];
    const context = {
      pending: { kind: "views", question, options: sourceIds.map((view_id) => ({ view_id })) },
      last_question: question,
      view_ids: sourceIds,
    };
    const messages = [{ role: "user", text: question }];
    const assistant = {
      role: "assistant",
      text: "Which source should I use?\n(ntw_regionBase; Population Estimates Statistics View;\nNSP Base View)",
    };
    const requests = [];
    globalThis.fetch = async (_url, options) => {
      requests.push(JSON.parse(options.body));
      return requests.length === 1
        ? sseResponse([["context", { conversation_context: context }], ["token", { text: assistant.text }], ["done", { question }]])
        : sseResponse([
          ...sourceIds.map((view_id) => ["view_result", { view_id, markdown: "Fixture regions" }]),
          ["done", { question: "all" }],
        ]);
    };
    await streamQuestion(question, "en", (event, payload) => {
      if (event === "context") assistant.conversationContext = payload.conversation_context;
    });
    messages.push(assistant);
    for (const reply of ["all", "provide for all"]) {
      const viewResults = [];
      await streamQuestion(reply, "en", (event, payload) => {
        if (event === "view_result") viewResults.push(payload);
      }, getLatestExportContext(messages), getConversationHistory(messages), getLatestConversationContext(messages));

      assert.deepEqual(requests.at(-1).conversation_context, context);
      assert.equal(requests.at(-1).question, reply);
      assert.equal(requests.at(-1).conversation_history[1].is_result, false);
      assert.deepEqual(viewResults.map(({ view_id }) => view_id), sourceIds);
    }
    assert.equal(getLatestConversationContext([{ role: "user", text: "A different conversation" }]), null);
  });

  it("sends bounded conversation history and context separately from export context", async () => {
    let request;
    globalThis.fetch = async (url, options) => {
      request = { url, body: JSON.parse(options.body) };
      return sseResponse([["done", { question: "q" }]]);
    };

    await streamQuestion(
      "Export these results to Excel.",
      "en",
      () => {},
      "ctx-token",
      [{ role: "user", text: "Show regions", is_result: false }],
      { pending: { kind: "views", source_ids: ["nsp_base_view_01"] } },
    );

    assert.ok(request.url.endsWith("/ask/stream"));
    assert.deepEqual(request.body, {
      question: "Export these results to Excel.",
      language: "en",
      export_context: "ctx-token",
      conversation_history: [{ role: "user", text: "Show regions", is_result: false }],
      conversation_context: { pending: { kind: "views", source_ids: ["nsp_base_view_01"] } },
    });
  });

  it("sends a null context when the conversation has no results yet", async () => {
    let body;
    globalThis.fetch = async (_url, options) => {
      body = JSON.parse(options.body);
      return sseResponse([["done", {}]]);
    };

    await streamQuestion("hello", "ar", () => {});

    assert.deepEqual(body, {
      question: "hello",
      language: "ar",
      export_context: null,
      conversation_history: [],
      conversation_context: null,
    });
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

  it("does not reuse earlier export context after a LOCAL SQL preview", () => {
    const messages = [assistant("1", "real-results"), { role: "assistant", sqlPreview: { status: "PREVIEW_ONLY" } }];
    assert.equal(getLatestExportContext(messages), null);
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

describe("conversation context selection", () => {
  it("uses the latest structured assistant context", () => {
    const messages = [
      { role: "assistant", text: "Choose a view", conversationContext: { pending: { source_ids: ["a"] } } },
      { role: "user", text: "both" },
      { role: "assistant", text: "Results", conversationContext: { pending: null, result: "r" } },
    ];
    assert.deepEqual(getLatestConversationContext(messages), { pending: null, result: "r" });
  });

  it("returns only the last bounded user and assistant messages", () => {
    const messages = Array.from({ length: 15 }, (_, index) => ({
      role: index % 2 ? "assistant" : "user",
      text: `message-${index}`,
    }));
    const history = getConversationHistory(messages, 4);

    assert.deepEqual(history.map(({ text }) => text), ["message-11", "message-12", "message-13", "message-14"]);
    assert.ok(history.every(({ is_result }) => !is_result));
  });

  it("uses displayed translations and marks actual result turns", () => {
    assert.deepEqual(
      getConversationHistory([
        {
          role: "assistant",
          text: "Total: 13",
          displayTranslations: { ar: "الإجمالي: 13" },
          conversationContext: { pending: null, last_question: "How many?", view_ids: ["nsp_base_view_01"] },
        },
      ], 12, "ar"),
      [{ role: "assistant", text: "الإجمالي: 13", is_result: true }],
    );
  });

  it("never marks a SQL preview as a result or carries an older context forward", () => {
    const messages = [
      { role: "assistant", text: "Actual results", conversationContext: { pending: null, last_question: "q", view_ids: ["nsp_base_view_01"] } },
      { role: "assistant", text: "Validated SQL preview only", sqlPreview: { status: "PREVIEW_ONLY" } },
    ];
    assert.equal(getLatestConversationContext(messages), null);
    assert.deepEqual(getConversationHistory(messages), [
      { role: "assistant", text: "Actual results", is_result: true },
      { role: "assistant", text: "Validated SQL preview only", is_result: false },
    ]);
  });
});

describe("multi-view source events", () => {
  it("keeps each view source and ignores duplicate SSE events", () => {
    const first = { dataset: "dw_nsp_api_views", object_name: "nsp_base_view_01" };
    const second = { dataset: "dw_sharepoint_views", object_name: "sopc_view_01" };
    const sources = mergeSources(mergeSources(mergeSources([], first), second), first);

    assert.deepEqual(sources, [first, second]);
  });
});
