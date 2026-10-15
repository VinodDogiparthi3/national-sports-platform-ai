import json
from dataclasses import dataclass

from config import MODEL
from conversation_context import history_payload
from schema_metadata import get_all_source_columns
from view_catalog import APPROVED_VIEWS, get_authorized_tables, get_source

MAX_COLUMNS_IN_ROUTING_PROMPT = 60


@dataclass(frozen=True)
class RouteResult:
    source_id: str | None = None
    clarification: str | None = None
    candidate_view_ids: tuple[str, ...] = ()

    @property
    def view_id(self) -> str | None:
        return self.source_id


def build_routing_prompt(question: str, language: str, columns_by_view: dict, history=()) -> str:
    entries = []
    for source in (*APPROVED_VIEWS, *get_authorized_tables()):
        names = [c[0] for c in columns_by_view.get(source.source_id, [])][:MAX_COLUMNS_IN_ROUTING_PROMPT]
        entries.append(
            {
                "source_id": source.source_id,
                "object_type": source.object_type,
                "dataset": source.dataset,
                "display_name": source.display_name,
                "domain": source.domain,
                "description": source.description,
                "columns": names,
                "available_years": list(getattr(source, "available_years", ())),
            }
        )
    response_language = "Arabic" if language == "ar" else "English"
    return f"""
You route questions to exactly one approved BigQuery view or explicitly authorized physical table of the National Sports Platform.

Available sources (JSON):
{json.dumps(entries, ensure_ascii=False)}

Recent conversation (JSON; use only when the current question depends on it):
{json.dumps(history_payload(history), ensure_ascii=False)}

Return only JSON: {{"source_id": <string or null>, "candidates": [<source_id>, ...], "clarification": <string or null>}}
- source_id: the single best source when the question clearly belongs to one source.
- If two or more sources plausibly fit and the question does not settle it, set source_id to null and list them in candidates.
- If the question is a standalone new request, route it independently instead of forcing it into prior context.
- If no source fits (unrelated question, greeting, schema/metadata request), set source_id to null, leave candidates empty.
- Use only source_id values from the list. Match on business domain, description and column names (Arabic or English).
- clarification: only when source_id is null; one short question in {response_language}.

Question: {json.dumps(question, ensure_ascii=False)}
"""


def parse_route(payload, no_match_message: str) -> RouteResult:
    if not isinstance(payload, dict):
        return RouteResult(clarification=no_match_message)

    source_id = payload.get("source_id") or payload.get("view_id")
    source = get_source(source_id or "")
    if source:
        return RouteResult(source_id=source.source_id)

    candidates = [
        source
        for source in (get_source(candidate) for candidate in (payload.get("candidates") or []) if isinstance(candidate, str))
        if source
    ]
    if len(candidates) > 1:
        options = "; ".join(source.display_name for source in candidates)
        question = payload.get("clarification")
        base = question if isinstance(question, str) and question.strip() else ""
        return RouteResult(
            clarification=f"{base} ({options})".strip(),
            candidate_view_ids=tuple(source.source_id for source in candidates),
        )

    clarification = payload.get("clarification")
    if isinstance(clarification, str) and clarification.strip():
        return RouteResult(clarification=clarification.strip())
    return RouteResult(clarification=no_match_message)


def route_question(question: str, language: str = "en", history=()) -> RouteResult:
    from gemini_service import (
        _parse_json_response,
        client,
        get_out_of_scope_clarification,
    )

    prompt = build_routing_prompt(question, language, get_all_source_columns(), history)
    response = client.models.generate_content(model=MODEL, contents=prompt)
    try:
        payload = _parse_json_response(response.text)
    except ValueError:
        payload = None
    return parse_route(payload, get_out_of_scope_clarification(language))
