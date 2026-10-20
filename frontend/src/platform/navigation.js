export function getUseCase(search) {
  return new URLSearchParams(search).get("usecase") === "uc2" ? "uc2" : "uc1";
}

export function buildUseCaseUrl(href, view) {
  if (!["uc1", "uc2"].includes(view)) throw new Error("Unknown use case.");
  const url = new URL(href);
  if (view === "uc2") url.searchParams.set("usecase", "uc2");
  else url.searchParams.delete("usecase");
  url.hash = "";
  return `${url.pathname}${url.search}${url.hash}`;
}

export function navigateUseCase(history, location, view) {
  if (getUseCase(location.search) === view) return false;
  history.pushState(history.state, "", buildUseCaseUrl(location.href, view));
  return true;
}