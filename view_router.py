import json
from dataclasses import dataclass

from config import MODEL
from schema_metadata import get_all_view_columns
from view_catalog import APPROVED_VIEWS, get_view

MAX_COLUMNS_IN_ROUTING_PROMPT = 60


@dataclass(frozen=True)
class RouteResult:
    view_id: str | None = None
    clarification: str | None = None


def build_routing_prompt(question: str, language: str, columns_by_view: dict) -> str:
    entries = []
    for view in APPROVED_VIEWS:
        names = [c[0] for c in columns_by_view.get(view.view, [])][:MAX_COLUMNS_IN_ROUTING_PROMPT]
        entries.append(
            {
                "view_id": view.view,
                "dataset": view.dataset,
                "display_name": view.display_name,
                "domain": view.domain,
                "description": view.description,
                "columns": names,
            }
        )
    response_language = "Arabic" if language == "ar" else "English"
    return f"""
You route questions to exactly one BigQuery view of the National Sports Platform.

Available views (JSON):
{json.dumps(entries, ensure_ascii=False)}

Return only JSON: {{"view_id": <string or null>, "candidates": [<view_id>, ...], "clarification": <string or null>}}
- view_id: the single best view when the question clearly belongs to one view.
- If two or more views plausibly fit and the question does not settle it, set view_id to null and list them in candidates.
- If no view fits (unrelated question, greeting, schema/metadata request), set view_id to null, leave candidates empty.
- Use only view_id values from the list. Match on business domain, description and column names (Arabic or English).
- clarification: only when view_id is null; one short question in {response_language}.

Question: {json.dumps(question, ensure_ascii=False)}
"""


def parse_route(payload, no_match_message: str) -> RouteResult:
    if not isinstance(payload, dict):
        return RouteResult(clarification=no_match_message)

    view = get_view(payload.get("view_id") or "")
    if view:
        return RouteResult(view_id=view.view)

    candidates = [
        v for v in (get_view(c) for c in (payload.get("candidates") or []) if isinstance(c, str)) if v
    ]
    if len(candidates) > 1:
        options = "; ".join(v.display_name for v in candidates)
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

    prompt = build_routing_prompt(question, language, get_all_view_columns())
    response = client.models.generate_content(model=MODEL, contents=prompt)
    try:
        payload = _parse_json_response(response.text)
    except ValueError:
        payload = None
    return parse_route(payload, get_out_of_scope_clarification(language))
