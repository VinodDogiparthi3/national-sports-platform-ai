// Messages belong to one conversation, so this only ever sees the selected conversation's results.
export function getLatestExportContext(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && message.exportContext) return message.exportContext;
  }
  return null;
}

export function resolveDownloadUrl(apiBaseUrl, downloadPath) {
  return `${apiBaseUrl}${downloadPath}`;
}
