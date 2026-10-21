// Messages belong to one conversation, so this only ever sees the selected conversation's results.
export function getLatestExportContext(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && message.sqlPreview) return null;
    if (message.role === "assistant" && message.exportContext) return message.exportContext;
  }
  return null;
}

export function getLatestConversationContext(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && message.sqlPreview) return null;
    if (message.role === "assistant" && message.conversationContext) {
      return message.conversationContext;
    }
  }
  return null;
}

export function mergeSources(current, incoming) {
  if (!incoming) return current;
  if (current.some((source) => source.dataset === incoming.dataset && source.object_name === incoming.object_name)) {
    return current;
  }
  return [...current, incoming];
}

export function getConversationHistory(messages, limit = 12, language = "en") {
  return messages
    .filter((message) => (message.role === "user" || message.role === "assistant") && message.text)
    .slice(-limit)
    .map((message) => ({
      role: message.role,
      text: (message.displayTranslations?.[language] || message.text).slice(0, 4000),
      is_result: message.role === "assistant" && Boolean(
        !message.sqlPreview && (
          message.exportContext
          || (message.conversationContext?.last_question && message.conversationContext.pending == null)
        )
      ),
    }));
}

export function resolveDownloadUrl(apiBaseUrl, downloadPath) {
  return `${apiBaseUrl}${downloadPath}`;
}
