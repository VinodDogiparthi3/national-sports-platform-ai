const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

export async function streamQuestion(question, onEvent) {
  const response = await fetch(`${API_BASE_URL}/ask/stream`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ question }),
  });

  if (!response.ok || !response.body) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `Request failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completion = null;

  const dispatch = (eventText) => {
    let event = "message";
    const data = [];

    for (const line of eventText.split(/\r?\n/)) {
      if (!line || line.startsWith(":")) continue;
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }

    if (!data.length) return;
    const payload = JSON.parse(data.join("\n"));
    if (event === "error") throw new Error(payload.message || "Unable to process the request.");
    if (event === "done") completion = payload;
    onEvent(event, payload);
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() || "";
      for (const eventText of events) dispatch(eventText);
    }

    buffer += decoder.decode();
    if (buffer.trim()) dispatch(buffer);
    if (!completion) throw new Error("The response stream ended before completion.");
    return completion;
  } finally {
    reader.releaseLock();
  }
}
