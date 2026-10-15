import json
from dataclasses import dataclass

from config import MODEL
from schema_metadata import get_all_source_columns
from view_catalog import APPROVED_VIEWS, get_approved_tables, get_source

MAX_COLUMNS_IN_ROUTING_PROMPT = 60


@dataclass(frozen=True)
class RouteResult:
    source_id: str | None = None
    clarification: str | None = None

    @property
    def view_id(self) -> str | None:
        return self.source_id


def build_routing_prompt(question: str, language: str, columns_by_view: dict) -> str:
    entries = []
    sources = [*APPROVED_VIEWS, *get_approved_tables()]
    for source in sources:
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
            }
        )
    response_language = "Arabic" if language == "ar" else "English"
    return f"""
You route questions to exactly one approved BigQuery view or physical table of the National Sports Platform.

Available sources (JSON):
{json.dumps(entries, ensure_ascii=False)}

Return only JSON: {{"source_id": <string or null>, "candidates": [<source_id>, ...], "clarification": <string or null>}}
- source_id: the single best source when the question clearly belongs to one source.
- If two or more sources plausibly fit and the question does not settle it, set source_id to null and list them in candidates.
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
        return RouteResult(clarification=f"{base} ({options})".strip())

    clarification = payload.get("clarification")
    if isinstance(clarification, str) and clarification.strip():
        return RouteResult(clarification=clarification.strip())
    return RouteResult(clarification=no_match_message)


def route_question(question: str, language: str = "en") -> RouteResult:
    from gemini_service import (
        _parse_json_response,
        client,
        get_out_of_scope_clarification,
    )

    prompt = build_routing_prompt(question, language, get_all_source_columns())
    response = client.models.generate_content(model=MODEL, contents=prompt)
    try:
        payload = _parse_json_response(response.text)
    except ValueError:
        payload = None
    return parse_route(payload, get_out_of_scope_clarification(language))
