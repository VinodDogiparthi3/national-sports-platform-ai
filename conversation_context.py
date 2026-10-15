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
    if kind not in {"views", "years"} or not options:
        return None

    normalized = normalize_reply(reply)
    normalized = normalized.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    both = normalized in {
        "both", "all", "both of them", "all of them", "both views", "all views",
        "كلاهما", "كليهما", "الاثنين", "كلا الخيارين", "كلا العرضين", "جميعها", "جميعهم", "الكل",
    }
    first = normalized in {"first", "the first", "first one", "option 1", "1", "الاول", "الاولى", "الخيار الاول", "الخيار الاولى"}
    second = normalized in {"second", "the second", "second one", "option 2", "2", "الثاني", "الثانية", "الخيار الثاني", "الخيار الثانية"}

    selected = []
    selected_year = None
    if kind == "years":
        year_match = re.search(r"\b(?:19|20)\d{2}\b", normalized)
        if year_match:
            selected_year = int(year_match.group())
            selected = [option for option in options if _as_mapping(option).get("year") == selected_year]
    if not selected:
        if both:
            selected = options
        elif first and options:
            selected = [options[0]]
        elif second and len(options) > 1:
            selected = [options[1]]

    resolved_views = []
    for option in selected:
        item = _as_mapping(option)
        source_id = item.get("source_id") or item.get("view_id")
        source = get_source(source_id or "")
        if source and source.source_id not in resolved_views:
            resolved_views.append(source.source_id)
            if selected_year is None and item.get("year") is not None:
                selected_year = int(item["year"])
    if not resolved_views:
        return None

    original_question = str(pending.get("question") or "").strip()
    if not original_question:
        return None
    if selected_year is not None and not re.search(r"\b(?:19|20)\d{2}\b", original_question):
        original_question = f"{original_question} for {selected_year}"

    return {
        "question": original_question,
        "view_ids": resolved_views,
        "year": selected_year,
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
    return bool(
        re.search(r"\b(explain|elaborate|interpret)\b", normalized)
        and re.search(r"\b(previous|above|earlier|last|results?|answer)\b", normalized)
        or re.search(r"اشرح|وضح|فسر", normalized)
        and re.search(r"السابق|أعلاه|السابقة|النتائج|الإجابة", normalized)
    )