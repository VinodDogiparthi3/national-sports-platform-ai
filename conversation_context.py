import re
import unicodedata

from view_catalog import get_source


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670\u0640]")


def _as_mapping(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value if isinstance(value, dict) else {}


def history_payload(history):
    result = []
    for item in history or []:
        message = _as_mapping(item)
        if message.get("role") not in {"user", "assistant"}:
            continue
        text = message.get("text")
        if isinstance(text, str) and text.strip():
            result.append({"role": message["role"], "text": text[:4000]})
    return result[-12:]


def previous_result_text(history) -> str | None:
    for item in reversed(history or []):
        message = _as_mapping(item)
        if message.get("role") == "assistant" and message.get("is_result"):
            text = message.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return None


def normalize_reply(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).translate(_ARABIC_DIGITS).casefold()
    value = _ARABIC_DIACRITICS.sub("", value)
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def resolve_pending_selection(reply: str, conversation_context):
    """Resolve a short selection against a prior, client-carried clarification."""
    context = _as_mapping(conversation_context)
    pending = _as_mapping(context.get("pending"))
    kind = pending.get("kind")
    options = pending.get("options") or []
    normalized = normalize_reply(reply)
    normalized = normalized.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    completed = not pending
    year_reply = re.fullmatch(r"(?:19|20)\d{2}", normalized)
    top_reply = re.fullmatch(r"(?:top|اعلى|افضل)\s+([1-9]\d?)", normalized)
    if completed:
        pending = {"question": context.get("last_question")}
        kind = "views"
        options = [{"view_id": source_id} for source_id in context.get("view_ids", [])]
    if kind not in {"views", "years"} or not options:
        return None

    both = normalized in {
        "both", "all", "provide for all", "both of them", "all of them", "both views", "all views",
        "كلاهما", "كليهما", "الاثنين", "كلا الخيارين", "كلا العرضين", "جميعها", "جميعهم", "الكل",
    }
    ordinals = (
        {"first", "1st", "the first", "first one", "option 1", "1", "الاول", "الاولى", "الخيار الاول", "الخيار الاولى"},
        {"second", "2nd", "the second", "second one", "option 2", "2", "الثاني", "الثانية", "الخيار الثاني", "الخيار الثانية"},
        {"third", "3rd", "the third", "third one", "option 3", "3", "الثالث", "الثالثة", "الخيار الثالث", "الخيار الثالثة"},
    )
    selection_text = re.sub(r"\s+و(?=ال)", " and ", normalized)
    parts = re.split(r"\s+(?:and|و)\s+", selection_text)
    indices = [next((index for index, aliases in enumerate(ordinals) if part in aliases), None) for part in parts]

    selected = []
    selected_year = None
    if completed and (year_reply or top_reply or normalized in {"compare", "قارن"}):
        selected = options
        if year_reply:
            selected_year = int(normalized)
    if kind == "years":
        year_match = re.search(r"\b(?:19|20)\d{2}\b", normalized)
        if year_match:
            selected_year = int(year_match.group())
            selected = [option for option in options if _as_mapping(option).get("year") == selected_year]
    if not selected:
        if both:
            selected = options
        elif all(index is not None and index < len(options) for index in indices):
            selected = [options[index] for index in indices]

    resolved_views = []
    years_by_view = {}
    for option in selected:
        item = _as_mapping(option)
        source_id = item.get("source_id") or item.get("view_id")
        source = get_source(source_id or "")
        if source and source.source_id not in resolved_views:
            resolved_views.append(source.source_id)
        if source and item.get("year") is not None:
            years_by_view.setdefault(source.source_id, []).append(int(item["year"]))
    if not resolved_views:
        return None

    original_question = str(pending.get("question") or "").strip()
    if not original_question:
        return None
    if selected_year is not None:
        if re.search(r"\b(?:19|20)\d{2}\b", original_question):
            original_question = re.sub(r"\b(?:19|20)\d{2}\b", str(selected_year), original_question)
        else:
            original_question = f"{original_question} for {selected_year}"
    if top_reply and completed:
        original_question = re.sub(r"\btop\s+\d+\b", "", original_question, flags=re.IGNORECASE).strip()
        original_question = f"{original_question}; top {top_reply.group(1)}"
    if completed and normalized in {"compare", "قارن"}:
        original_question = f"Compare the results for: {original_question}"

    questions_by_view = {}
    for source_id, years in years_by_view.items():
        question = re.sub(r"\b(?:19|20)\d{2}\b", "", str(pending.get("question") or "")).strip()
        question = re.sub(r"\s+for\s*$", "", question, flags=re.IGNORECASE)
        questions_by_view[source_id] = f"{question} for {' and '.join(str(year) for year in sorted(set(years)))}"
    if len(resolved_views) == 1 and resolved_views[0] in questions_by_view:
        original_question = questions_by_view[resolved_views[0]]

    return {
        "question": original_question,
        "view_ids": resolved_views,
        "year": selected_year,
        "questions_by_view": questions_by_view,
    }


def translation_target(question: str, current_language: str) -> str | None:
    normalized = normalize_reply(question)
    has_translation = bool(re.search(r"\btranslate\b|\btranslation\b|ترجم|ترجمة", normalized))
    has_previous_reference = bool(
        re.search(r"\b(previous|above|earlier|last)\b|\bresults?\b|\banswer\b|السابق|أعلاه|السابقة|النتائج|الإجابة", normalized)
    )
    if not has_translation or not has_previous_reference:
        return None

    if re.search(r"\b(?:english|to english|into english)\b|للإنجليزية|إلى الإنجليزية|بالإنجليزية", normalized):
        return "en"
    if re.search(r"\b(?:arabic|to arabic|into arabic)\b|للعربية|إلى العربية|بالعربية", normalized):
        return "ar"
    return "en" if current_language == "ar" else "ar"


def is_explanation_request(question: str) -> bool:
    normalized = normalize_reply(question)
    if normalized in {"explain", "why", "explain why", "اشرح", "وضح", "فسر", "لماذا"}:
        return True
    return bool(
        re.search(r"\b(explain|elaborate|interpret|why)\b", normalized)
        and re.search(r"\b(previous|above|earlier|last|results?|answer)\b", normalized)
        or re.search(r"اشرح|وضح|فسر", normalized)
        and re.search(r"السابق|أعلاه|السابقة|النتائج|الإجابة", normalized)
    )