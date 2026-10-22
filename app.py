from difflib import get_close_matches
from datetime import datetime, timezone
import json
import logging
import os
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

import sqlglot
from fastapi import FastAPI, HTTPException
from google.api_core import exceptions as api_exceptions
from google.genai import errors as genai_errors
from sqlglot import exp
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from bigquery_database import (
    execute_sql_batch,
    get_export_row_counts,
    stream_query_rows_batch,
    QueryBudgetExceeded,
)
from config import (
    EXPORT_CONTEXT_TTL_SECONDS,
    EXPORT_DOWNLOAD_TTL_SECONDS,
    EXPORT_MAX_ROWS,
    PERSONAL_GCP_ALLOW_REMOTE_CLIENTS,
    PERSONAL_GCP_API_TOKEN,
    PERSONAL_GCP_EXECUTION_ENABLED,
    PERSONAL_GCP_RATE_LIMIT_PER_MINUTE,
    SCHEMA_PROVIDER,
)
from excel_export import (
    XLSX_MEDIA_TYPE,
    ExportTokenError,
    ExportTooLargeError,
    decrypt_token,
    encrypt_token,
    friendly_header,
    is_excel_export_request,
    write_workbook,
    write_workbook_sheets,
)
from gemini_service import (
    OUT_OF_SCOPE_CLARIFICATION,
    extract_export_analysis_question,
    explain_previous_results,
    generate_routed_sql,
    generate_query_plan,
    generate_answer,
    generate_answer_stream,
    get_out_of_scope_clarification,
    translate_texts,
    translate_result_preserving_numbers,
    translate_questions_to_arabic,
)
from sql_guardrails import validate_sql
from request_safety import RequestSafetyMiddleware
from usage_limits import UsageLimitExceeded
from view_catalog import find_source
from conversation_context import is_explanation_request, previous_result_text, resolve_pending_selection, translation_target
from schema_provider import SchemaNotFoundError, UnauthorizedSchemaSourceError


# --------------------------------------------------
# FastAPI
# --------------------------------------------------

app = FastAPI(
    title="National Sports Platform AI API",
    version="1.0.0",
)
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


@app.exception_handler(UsageLimitExceeded)
async def usage_limit_exceeded(_request, error: UsageLimitExceeded):
    return JSONResponse({"detail": str(error)}, status_code=429)


# Added before CORS so CORS stays outermost and rejections still carry CORS headers.
app.add_middleware(
    RequestSafetyMiddleware,
    enforce_personal_guard=SCHEMA_PROVIDER == "PERSONAL_GCP",
    allow_remote_clients=PERSONAL_GCP_ALLOW_REMOTE_CLIENTS,
    api_token=PERSONAL_GCP_API_TOKEN,
    rate_limit_per_minute=PERSONAL_GCP_RATE_LIMIT_PER_MINUTE,
)


# --------------------------------------------------
# CORS Configuration
# --------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# --------------------------------------------------
# Request Model
# --------------------------------------------------

class QuestionRequest(BaseModel):
    question: str
    language: Literal["en", "ar"] = "en"
    export_context: str | None = Field(default=None, max_length=20000)
    conversation_history: list["ConversationTurn"] = Field(default_factory=list, max_length=12)
    conversation_context: "ConversationContext | None" = None


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)
    is_result: bool = False


class SelectionOption(BaseModel):
    view_id: str = Field(max_length=200)
    year: int | None = None


class PendingSelection(BaseModel):
    kind: Literal["views", "years", "details"]
    question: str = Field(max_length=4000)
    options: list[SelectionOption] = Field(max_length=10)
    clarification: str | None = Field(default=None, max_length=4000)


class ConversationContext(BaseModel):
    pending: PendingSelection | None = None
    last_question: str | None = Field(default=None, max_length=4000)
    view_ids: list[str] = Field(default_factory=list, max_length=10)


class QuestionTranslationRequest(BaseModel):
    questions: list[str]


class TextTranslationRequest(BaseModel):
    texts: list[str]
    target_language: Literal["en", "ar"]


def get_response_language(question: str, language: str) -> str:
    if language == "ar" or re.search(r"[\u0600-\u06ff]", question):
        return "ar"
    return "en"


def get_greeting_response(question: str, language: str = "en") -> str | None:
    normalized = " ".join(question.casefold().split()).strip(".,!?;:؟،")
    english_greeting = "Hello! How can I help you explore National Sports Platform data?"
    arabic_greeting = "مرحبًا! كيف يمكنني مساعدتك في استكشاف بيانات المنصة الوطنية للرياضة؟"
    responses = {
        "hi": english_greeting,
        "hi there": english_greeting,
        "hello": english_greeting,
        "hello there": english_greeting,
        "hey": english_greeting,
        "hey there": english_greeting,
        "hiya": english_greeting,
        "howdy": english_greeting,
        "greetings": english_greeting,
        "good morning": "Good morning! How can I help you explore National Sports Platform data?",
        "good afternoon": "Good afternoon! How can I help you explore National Sports Platform data?",
        "good evening": "Good evening! How can I help you explore National Sports Platform data?",
        "good day": english_greeting,
        "good night": "Good evening! How can I help you explore National Sports Platform data?",
        "morning": "Good morning! How can I help you explore National Sports Platform data?",
        "afternoon": "Good afternoon! How can I help you explore National Sports Platform data?",
        "evening": "Good evening! How can I help you explore National Sports Platform data?",
        "what's up": english_greeting,
        "whats up": english_greeting,
        "how are you": "I'm doing well, thanks! How can I help you explore National Sports Platform data?",
        "how's it going": "I'm doing well, thanks! How can I help you explore National Sports Platform data?",
        "hows it going": "I'm doing well, thanks! How can I help you explore National Sports Platform data?",
        "مرحبا": arabic_greeting,
        "أهلا": arabic_greeting,
        "أهلا وسهلا": arabic_greeting,
        "السلام عليكم": "وعليكم السلام! كيف يمكنني مساعدتك في استكشاف بيانات المنصة الوطنية للرياضة؟",
    }
    if normalized in responses:
        response = responses[normalized]
        if language == "ar" and not re.search(r"[\u0600-\u06ff]", response):
            return arabic_greeting
        return response

    if len(normalized) > 40 or len(normalized.split()) > 5:
        return None

    closest_match = get_close_matches(
        normalized,
        responses,
        n=1,
        cutoff=0.88,
    )
    if closest_match:
        return arabic_greeting if language == "ar" else responses[closest_match[0]]
    return None


def get_source_info(sql: str) -> dict | None:
    """Dataset/view actually used by a validated query, for display in the UI."""
    try:
        tables = list(sqlglot.parse_one(sql, read="bigquery").find_all(exp.Table))
    except Exception:
        return None
    views = []
    for table in tables:
        source = find_source(table.catalog or "", table.db or "", table.name or "")
        if source and source not in views:
            views.append(source)
    if not views:
        return None
    first = views[0]
    return {
        "project": first.project,
        "dataset": first.dataset,
        "view": getattr(first, "view", first.source_id),
        "object_type": first.object_type,
        "object_name": getattr(first, "table", getattr(first, "name", first.source_id)),
        "display_name": first.display_name,
        "domain": first.domain,
        "sql": sql,
    }


NO_DATA_MESSAGES = {
    "en": "No matching data was found for this question.",
    "ar": "لم يتم العثور على بيانات مطابقة لهذا السؤال.",
}

NO_PREVIOUS_RESULT_MESSAGES = {
    "en": "There are no previous results in this conversation to use.",
    "ar": "لا توجد نتائج سابقة في هذه المحادثة لاستخدامها.",
}

LOCAL_SCHEMA_UNAVAILABLE_MESSAGES = {
    "en": "I can't generate a preview because the selected source has no approved schema in the local backup. Please ask about an available approved source.",
    "ar": "لا يمكنني إنشاء معاينة لأن المصدر المحدد لا يملك مخططًا معتمدًا في النسخة المحلية. يرجى السؤال عن مصدر معتمد متاح.",
}

LOCAL_PREVIEW_ONLY_MESSAGES = {
    "en": "This LOCAL workspace only provides validated SQL previews. No query was executed, so there are no results to explain, translate, or export.",
    "ar": "توفر مساحة LOCAL معاينات SQL معتمدة فقط. لم يتم تنفيذ أي استعلام، لذلك لا توجد نتائج لشرحها أو ترجمتها أو تصديرها.",
}

LOCAL_PREVIEW_NOTICE = {
    "en": "No query executed; no results available.",
    "ar": "لم يتم تنفيذ أي استعلام؛ لا توجد نتائج متاحة.",
}

BUDGET_EXCEEDED_MESSAGES = {
    "en": (
        "This request was blocked because its combined BigQuery estimate exceeds the 250 MiB limit. "
        "Narrow the request by year, region, sport, or another filter and try again."
    ),
    "ar": (
        "تم إيقاف الطلب لأن تقدير استعلامات BigQuery المجمّع يتجاوز حد 250 MiB. "
        "يرجى تضييق الطلب حسب السنة أو المنطقة أو الرياضة أو مرشح آخر ثم المحاولة مجددًا."
    ),
}

SQL_VALIDATION_MESSAGES = {
    "en": "The generated SQL did not pass safety validation. Please rephrase your data request and try again.",
    "ar": "لم يجتز الاستعلام الذي تم إنشاؤه فحص الأمان. يرجى إعادة صياغة طلب البيانات والمحاولة مرة أخرى.",
}

ERROR_MESSAGES = {
    "permission": {
        "en": "The service does not have permission to read the required BigQuery data.",
        "ar": "لا تملك الخدمة صلاحية قراءة بيانات BigQuery المطلوبة.",
    },
    "gemini": {
        "en": "The AI service is temporarily unavailable. Please try again.",
        "ar": "خدمة الذكاء الاصطناعي غير متاحة مؤقتًا. يرجى المحاولة مرة أخرى.",
    },
    "bigquery": {
        "en": "The query could not be executed in BigQuery. Please rephrase your question.",
        "ar": "تعذر تنفيذ الاستعلام في BigQuery. يرجى إعادة صياغة السؤال.",
    },
    "generic": {
        "en": "Unable to process the request.",
        "ar": "تعذر معالجة الطلب. يرجى المحاولة مرة أخرى.",
    },
}


def get_error_message(error: Exception, language: str) -> str:
    if isinstance(error, api_exceptions.Forbidden):
        kind = "permission"
    elif isinstance(error, genai_errors.APIError):
        kind = "gemini"
    elif isinstance(error, api_exceptions.GoogleAPICallError):
        kind = "bigquery"
    else:
        kind = "generic"
    return ERROR_MESSAGES[kind]["ar" if language == "ar" else "en"]


METADATA_QUESTION_MARKERS = (
    "row count",
    "rows count",
    "count of rows",
    "count rows",
    "how many rows",
    "number of rows",
    "total rows",
    "record count",
    "count of records",
    "how many records",
    "number of records",
    "total records",
    "how many columns",
    "what columns",
    "which columns",
    "list columns",
    "show columns",
    "available columns",
    "column names",
    "what fields",
    "which fields",
    "field names",
    "schema",
    "information schema",
    "table structure",
    "database structure",
    "table information",
    "database information",
    "عدد الصفوف",
    "عدد السجلات",
    "عدد الأعمدة",
    "أسماء الأعمدة",
    "مخطط البيانات",
    "هيكل الجدول",
)


def is_metadata_question(question: str) -> bool:
    normalized = " ".join(re.sub(r"[^\w]+", " ", question.casefold()).split())
    return any(marker in normalized for marker in METADATA_QUESTION_MARKERS)


EXPORT_MESSAGES = {
    "en": {
        "ready": "Your Excel report is ready.",
        "no_context": (
            "Which NSP data would you like to export to Excel? Ask a data question first, "
            "for example \"How many registrations are there for each sport?\", and then request the export."
        ),
        "no_data": "No matching data was found, so no Excel file was created.",
        "too_large": (
            "This export has {rows:,} rows, which is above the {limit:,}-row limit. "
            "Please narrow the request, for example by sport, region, or year."
        ),
        "too_large_query": (
            "This export is too large to process. "
            "Please narrow the request, for example by sport, region, or year."
        ),
        "failed": "I couldn't generate the Excel file. Please try again.",
    },
    "ar": {
        "ready": "تقرير Excel جاهز.",
        "no_context": (
            "ما بيانات المنصة الوطنية للرياضة (NSP) التي تريد تصديرها إلى Excel؟ "
            "اطرح سؤالاً عن البيانات أولاً، مثل «كم عدد التسجيلات لكل رياضة؟»، ثم اطلب تصديرها."
        ),
        "no_data": "لم يتم العثور على بيانات مطابقة، لذلك لم يتم إنشاء ملف Excel.",
        "too_large": (
            "يحتوي هذا التصدير على {rows:,} صفًا، وهو يتجاوز الحد المسموح به ({limit:,} صف). "
            "يرجى تضييق الطلب، مثلًا حسب الرياضة أو المنطقة أو السنة."
        ),
        "too_large_query": (
            "حجم هذا التصدير كبير جدًا للمعالجة. "
            "يرجى تضييق الطلب، مثلًا حسب الرياضة أو المنطقة أو السنة."
        ),
        "failed": "تعذر إنشاء ملف Excel. يرجى المحاولة مرة أخرى.",
    },
}


def _export_context_token(queries, question: str) -> str:
    if isinstance(queries, str):
        queries = [{"view_id": None, "sql": queries}]
    payload = {"kind": "context", "queries": queries, "question": question}
    if len(queries) == 1:
        payload["sql"] = queries[0]["sql"] if isinstance(queries[0], dict) else queries[0][1]
    return encrypt_token(payload)


def _queries_from_export_context(context):
    queries = context.get("queries") if isinstance(context, dict) else None
    if queries:
        return [(item.get("view_id"), item["sql"]) for item in queries if isinstance(item, dict) and isinstance(item.get("sql"), str)]
    if isinstance(context, dict) and isinstance(context.get("sql"), str):
        return [(None, context["sql"])]
    return []


def _context_dict(context):
    return context.model_dump() if hasattr(context, "model_dump") else context


def _planning_history(history):
    return () if SCHEMA_PROVIDER == "LOCAL" else history


def _planning_context(context):
    if SCHEMA_PROVIDER != "LOCAL":
        return context
    data = _context_dict(context) or {}
    return context if data.get("pending") else None


def _log_query_stage(request_id: str, stage: str, *, context=None, selection=None, query_count: int = 0, reason: str | None = None, source_position: int | None = None):
    data = _context_dict(context) or {}
    pending = data.get("pending") or {}
    record = {
        "event": "query_stage",
        "request_id": request_id,
        "stage": stage,
        "has_context": bool(data),
        "pending_option_count": len(pending.get("options") or []),
        "selection_resolved": selection is not None,
        "selected_source_count": len(selection.get("view_ids") or []) if selection else 0,
        "query_count": query_count,
        "reason": reason,
    }
    if source_position is not None:
        record.update(source_position=source_position, source_status="needs_clarification")
    logging.getLogger("uvicorn.error").info(json.dumps(record))


def _source_records(queries):
    return [
        source
        for _, sql in queries
        if (source := get_source_info(sql)) is not None
    ]


def _local_sql_preview(safe_queries, language: str):
    if not safe_queries:
        raise ValueError("A SQL preview requires at least one validated query.")
    preview_queries = []
    authorized_source_ids = []
    for planned_source_id, sql in safe_queries:
        try:
            tables = sqlglot.parse_one(sql, read="bigquery").find_all(exp.Table)
        except Exception as error:
            raise ValueError("Validated SQL could not be resolved to authorized sources.") from error
        source_ids = []
        for table in tables:
            source = find_source(table.catalog or "", table.db or "", table.name or "")
            if source is None:
                raise ValueError("Validated SQL references an unauthorized source.")
            if source.source_id not in source_ids:
                source_ids.append(source.source_id)
        if source_ids != [planned_source_id]:
            raise ValueError("Validated SQL source does not match its authorized plan.")
        preview_queries.append({"source_id": planned_source_id, "sql": sql})
        if planned_source_id not in authorized_source_ids:
            authorized_source_ids.append(planned_source_id)

    from schema_metadata import get_local_schema_backup_metadata

    return {
        "status": "PREVIEW_ONLY",
        "notice": LOCAL_PREVIEW_NOTICE[language],
        "queries": preview_queries,
        "authorized_source_ids": authorized_source_ids,
        "schema_backup": get_local_schema_backup_metadata(),
    }


def _format_view_answer(view_id: str, answer: str) -> str:
    from view_catalog import get_source

    source = get_source(view_id)
    return f"### {source.display_name if source else view_id}\n\n{answer}"


def _localized_headers(columns: list[str], language: str) -> list[str]:
    headers = [friendly_header(column) for column in columns]
    if language == "ar":
        try:
            translated = translate_texts(headers, "ar")
            if len(translated) == len(headers):
                return translated
        except Exception as e:
            print("\nERROR TRANSLATING EXPORT HEADERS:", type(e).__name__, str(e))
    return headers


def _remove_file(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


def _excel_export_events(request: QuestionRequest, response_language: str):
    """Handle an Excel export request: reuse the previous validated query or run the one in the message."""
    messages = EXPORT_MESSAGES[response_language]
    done = _sse_event("done", {"question": request.question})

    if SCHEMA_PROVIDER in {"LOCAL", "PERSONAL_GCP"}:
        yield _sse_event("token", {"text": LOCAL_PREVIEW_ONLY_MESSAGES[response_language]})
        yield done
        return

    try:
        context = None
        if request.export_context:
            try:
                context = decrypt_token(
                    request.export_context, EXPORT_CONTEXT_TTL_SECONDS, "context"
                )
            except ExportTokenError:
                context = None

        try:
            analysis_question = extract_export_analysis_question(
                request.question, context["question"] if context else None
            )
        except Exception as e:
            print("\nERROR SPLITTING EXPORT REQUEST:", type(e).__name__, str(e))
            analysis_question = None

        new_context_queries = None
        if analysis_question:
            if is_metadata_question(analysis_question):
                yield _sse_event("token", {"text": get_out_of_scope_clarification(response_language)})
                yield done
                return

            plan = generate_query_plan(
                analysis_question,
                response_language,
                request.conversation_history,
                request.conversation_context,
            )
            if plan.clarification:
                yield _sse_event(
                    "token",
                    {"text": plan.clarification},
                )
                yield done
                return
            source_queries = new_context_queries = list(plan.queries)
        elif context:
            source_queries = _queries_from_export_context(context)
        else:
            yield _sse_event("token", {"text": messages["no_context"]})
            yield done
            return

        try:
            export_queries = [
                (view_id, validate_sql(sql, row_limit=None))
                for view_id, sql in source_queries
            ]
        except ValueError:
            yield _sse_event("token", {"text": get_out_of_scope_clarification(response_language)})
            yield done
            return
        if not export_queries:
            yield _sse_event("token", {"text": messages["no_context"]})
            yield done
            return

        yield _sse_event("status", {"step": "analyzing"})
        try:
            row_counts = get_export_row_counts([sql for _, sql in export_queries])
        except ValueError:
            yield _sse_event("token", {"text": messages["too_large_query"]})
            yield done
            return

        if not any(row_counts):
            yield _sse_event("token", {"text": messages["no_data"]})
            yield done
            return
        row_count = sum(row_counts)
        if row_count > EXPORT_MAX_ROWS:
            yield _sse_event(
                "token",
                {"text": messages["too_large"].format(rows=row_count, limit=EXPORT_MAX_ROWS)},
            )
            yield done
            return

        download_token = encrypt_token({
            "kind": "download",
            "queries": [{"view_id": view_id, "sql": sql} for view_id, sql in export_queries],
            "language": response_language,
        })
        yield _sse_event("token", {"text": messages["ready"]})
        yield _sse_event(
            "export",
            {"download_url": f"/exports/{download_token}", "row_count": row_count},
        )
        if new_context_queries:
            yield _sse_event(
                "context",
                {
                    "export_context": _export_context_token(
                        [{"view_id": view_id, "sql": sql} for view_id, sql in new_context_queries],
                        analysis_question,
                    ),
                    "conversation_context": plan.conversation_context,
                },
            )
        yield done

    except Exception as e:
        print("\nERROR IN EXCEL EXPORT:")
        print(type(e).__name__)
        print(str(e))
        yield _sse_event("token", {"text": messages["failed"]})
        yield done


# --------------------------------------------------
# API Endpoints
# --------------------------------------------------

@app.get("/")
def root():
    index_file = FRONTEND_DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)

    return {
        "message": "National Sports Platform AI API is running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "schema_provider": SCHEMA_PROVIDER,
    }


@app.get("/version")
def version():
    return {
        "version": app.version,
        "service": os.getenv("K_SERVICE", "local"),
        "revision": os.getenv("K_REVISION", "local"),
    }


@app.post("/translate/questions")
def translate_questions(request: QuestionTranslationRequest):
    if SCHEMA_PROVIDER == "PERSONAL_GCP" and not PERSONAL_GCP_EXECUTION_ENABLED:
        raise HTTPException(status_code=503, detail="PERSONAL_GCP execution is disabled in this checkpoint.")
    if len(request.questions) > 50:
        raise HTTPException(
            status_code=400,
            detail="Too many questions to translate at once.",
        )
    try:
        return {"translations": translate_questions_to_arabic(request.questions)}
    except Exception as e:
        print("\nERROR IN /translate/questions:")
        print(type(e).__name__)
        print(str(e))
        raise HTTPException(
            status_code=502,
            detail="Question translation is temporarily unavailable.",
        )


@app.post("/translate/texts")
def translate_chat_texts(request: TextTranslationRequest):
    if SCHEMA_PROVIDER == "PERSONAL_GCP" and not PERSONAL_GCP_EXECUTION_ENABLED:
        raise HTTPException(status_code=503, detail="PERSONAL_GCP execution is disabled in this checkpoint.")
    if len(request.texts) > 50:
        raise HTTPException(
            status_code=400,
            detail="Too many messages to translate at once.",
        )
    try:
        return {
            "translations": translate_texts(request.texts, request.target_language)
        }
    except Exception as e:
        print("\nERROR IN /translate/texts:")
        print(type(e).__name__)
        print(str(e))
        raise HTTPException(
            status_code=502,
            detail="Conversation translation is temporarily unavailable.",
        )


@app.get("/exports/{token}")
def download_excel_export(token: str):
    if SCHEMA_PROVIDER in {"LOCAL", "PERSONAL_GCP"}:
        raise HTTPException(status_code=403, detail=LOCAL_PREVIEW_ONLY_MESSAGES["en"])
    try:
        payload = decrypt_token(token, EXPORT_DOWNLOAD_TTL_SECONDS, "download")
    except ExportTokenError:
        raise HTTPException(
            status_code=410,
            detail=(
                "This download link is invalid or has expired. Please request the export again. "
                "رابط التنزيل غير صالح أو انتهت صلاحيته. يرجى طلب التصدير مرة أخرى."
            ),
        )

    language = "ar" if payload.get("language") == "ar" else "en"
    path = None
    try:
        source_queries = payload.get("queries") or [{"sql": payload["sql"], "view_id": None}]
        export_queries = [
            (query.get("view_id"), validate_sql(query["sql"], row_limit=None))
            for query in source_queries
        ]
        query_rows = stream_query_rows_batch([sql for _, sql in export_queries])
        sheets = []
        for (view_id, _), (columns, rows) in zip(export_queries, query_rows):
            source = None
            if view_id:
                from view_catalog import get_source

                source = get_source(view_id)
            title = source.display_name if source else "Data"
            sheets.append((title, _localized_headers(columns, language), rows))
        path, _ = write_workbook_sheets(sheets, language=language, max_rows=EXPORT_MAX_ROWS)
    except ExportTooLargeError:
        raise HTTPException(status_code=413, detail=EXPORT_MESSAGES[language]["too_large_query"])
    except ValueError:
        raise HTTPException(status_code=400, detail=EXPORT_MESSAGES[language]["too_large_query"])
    except Exception as e:
        print("\nERROR IN /exports:")
        print(type(e).__name__)
        print(str(e))
        raise HTTPException(status_code=500, detail=EXPORT_MESSAGES[language]["failed"])

    filename = datetime.now(timezone.utc).strftime("NSP_Report_%Y%m%d_%H%M%S.xlsx")
    return FileResponse(
        path,
        media_type=XLSX_MEDIA_TYPE,
        filename=filename,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(_remove_file, path),
    )


@app.post("/ask")
def ask(request: QuestionRequest):
    if SCHEMA_PROVIDER == "PERSONAL_GCP" and not PERSONAL_GCP_EXECUTION_ENABLED:
        raise HTTPException(status_code=503, detail="PERSONAL_GCP execution is disabled in this checkpoint.")
    request_id = uuid4().hex
    _log_query_stage(request_id, "received", context=request.conversation_context)
    try:
        response_language = get_response_language(request.question, request.language)
        clarification = get_out_of_scope_clarification(response_language)
        greeting_response = get_greeting_response(request.question, response_language)
        if greeting_response:
            return {
                "question": request.question,
                "answer": greeting_response,
            }

        if SCHEMA_PROVIDER == "LOCAL" and (
            translation_target(request.question, response_language)
            or is_explanation_request(request.question)
        ):
            return {"question": request.question, "answer": LOCAL_PREVIEW_ONLY_MESSAGES[response_language]}
        if SCHEMA_PROVIDER == "LOCAL" and is_excel_export_request(request.question):
            return {"question": request.question, "answer": LOCAL_PREVIEW_ONLY_MESSAGES[response_language]}

        previous_result = previous_result_text(request.conversation_history)
        target_language = translation_target(request.question, response_language)
        if target_language:
            answer = (
                translate_result_preserving_numbers(previous_result, target_language)
                if previous_result
                else NO_PREVIOUS_RESULT_MESSAGES[response_language]
            )
            return {"question": request.question, "answer": answer}

        if is_explanation_request(request.question):
            answer = (
                explain_previous_results(request.question, previous_result, response_language)
                if previous_result
                else NO_PREVIOUS_RESULT_MESSAGES[response_language]
            )
            return {"question": request.question, "answer": answer}

        planning_context = _planning_context(request.conversation_context)
        selection = resolve_pending_selection(request.question, planning_context)
        _log_query_stage(request_id, "selection_checked", context=request.conversation_context, selection=selection)
        if not selection and is_metadata_question(request.question):
            _log_query_stage(request_id, "request_classified", reason="metadata_request")
            return {
                "question": request.question,
                "answer": clarification,
            }

        # 1. Natural language -> BigQuery SQL
        _log_query_stage(request_id, "planning_started", selection=selection)
        try:
            plan = generate_query_plan(
                request.question,
                response_language,
                _planning_history(request.conversation_history),
                planning_context,
            )
        except (SchemaNotFoundError, UnauthorizedSchemaSourceError):
            if SCHEMA_PROVIDER != "LOCAL":
                raise
            return {"question": request.question, "answer": LOCAL_SCHEMA_UNAVAILABLE_MESSAGES[response_language]}

        if plan.clarification:
            _log_query_stage(request_id, "clarification_sent", context=plan.conversation_context,
                             source_position=plan.clarification_source_position,
                             reason="source_planning_clarification" if plan.clarification_source_position else None)
            return {
                "question": request.question,
                "answer": plan.clarification,
                **({"conversation_context": plan.conversation_context} if plan.conversation_context else {}),
            }

        # Validate every view query before any of them can execute.
        try:
            safe_queries = [(view_id, validate_sql(sql)) for view_id, sql in plan.queries]
        except (SchemaNotFoundError, UnauthorizedSchemaSourceError):
            if SCHEMA_PROVIDER != "LOCAL":
                raise
            return {"question": request.question, "answer": LOCAL_SCHEMA_UNAVAILABLE_MESSAGES[response_language]}
        except ValueError:
            context = _context_dict(request.conversation_context) or plan.conversation_context
            _log_query_stage(request_id, "validation_rejected", context=context, selection=selection,
                             query_count=len(plan.queries), reason="sql_guardrail_rejection")
            return {
                "question": request.question,
                "answer": SQL_VALIDATION_MESSAGES[response_language],
                **({"conversation_context": context} if context else {}),
            }

        if SCHEMA_PROVIDER == "LOCAL":
            try:
                preview = _local_sql_preview(safe_queries, response_language)
            except (SchemaNotFoundError, UnauthorizedSchemaSourceError):
                return {"question": request.question, "answer": LOCAL_SCHEMA_UNAVAILABLE_MESSAGES[response_language]}
            except Exception:
                return {"question": request.question, "answer": SQL_VALIDATION_MESSAGES[response_language]}
            _log_query_stage(request_id, "preview_ready", context=plan.conversation_context, query_count=len(safe_queries))
            return {"question": request.question, "sql_preview": preview}

        _log_query_stage(request_id, "execution_requested", selection=selection, query_count=len(safe_queries))
        results = execute_sql_batch([sql for _, sql in safe_queries])
        sections = []
        for (view_id, _), (columns, rows) in zip(safe_queries, results):
            answer = generate_answer(request.question, columns, rows, response_language) if rows else NO_DATA_MESSAGES[response_language]
            sections.append(_format_view_answer(view_id, answer) if len(safe_queries) > 1 else answer)
        sources = _source_records(safe_queries)
        response = {
            "question": request.question,
            "answer": "\n\n".join(sections),
        }
        if len(sources) == 1:
            response["source"] = sources[0]
        elif sources:
            response["sources"] = sources
        if plan.conversation_context:
            response["conversation_context"] = plan.conversation_context
        _log_query_stage(request_id, "completed", context=plan.conversation_context, query_count=len(safe_queries))
        return response

    except QueryBudgetExceeded:
        _log_query_stage(request_id, "budget_rejected", reason="combined_budget_limit")
        return {
            "question": request.question,
            "answer": BUDGET_EXCEEDED_MESSAGES[response_language],
        }

    except ValueError as e:
        _log_query_stage(request_id, "request_failed", reason="invalid_request")
        raise HTTPException(
            status_code=400,
            detail=(
                "تعذر معالجة سؤال بيانات المنصة. يرجى إعادة صياغته."
                if response_language == "ar"
                else "I couldn't process that NSP data question. Please try rephrasing it."
            ),
        )

    except Exception as e:
        _log_query_stage(request_id, "request_failed", reason="upstream_failure")

        raise HTTPException(
            status_code=500,
            detail=get_error_message(e, response_language),
        )


def _sse_event(event: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


@app.post("/ask/stream")
def ask_stream(request: QuestionRequest):
    request_id = uuid4().hex

    def event_stream():
        if SCHEMA_PROVIDER == "PERSONAL_GCP" and not PERSONAL_GCP_EXECUTION_ENABLED:
            yield _sse_event("error", {"message": "PERSONAL_GCP execution is disabled in this checkpoint."})
            return
        _log_query_stage(request_id, "received", context=request.conversation_context)
        try:
            response_language = get_response_language(request.question, request.language)
            clarification = get_out_of_scope_clarification(response_language)
            yield _sse_event("status", {"step": "understanding"})

            greeting_response = get_greeting_response(request.question, response_language)
            if greeting_response:
                yield _sse_event("token", {"text": greeting_response})
                yield _sse_event("done", {"question": request.question})
                return

            if SCHEMA_PROVIDER == "LOCAL" and (
                translation_target(request.question, response_language)
                or is_explanation_request(request.question)
            ):
                yield _sse_event("token", {"text": LOCAL_PREVIEW_ONLY_MESSAGES[response_language]})
                yield _sse_event("done", {"question": request.question})
                return
            if SCHEMA_PROVIDER == "LOCAL" and is_excel_export_request(request.question):
                yield _sse_event("token", {"text": LOCAL_PREVIEW_ONLY_MESSAGES[response_language]})
                yield _sse_event("done", {"question": request.question})
                return

            previous_result = previous_result_text(request.conversation_history)
            target_language = translation_target(request.question, response_language)
            if target_language:
                answer = (
                    translate_result_preserving_numbers(previous_result, target_language)
                    if previous_result
                    else NO_PREVIOUS_RESULT_MESSAGES[response_language]
                )
                yield _sse_event("token", {"text": answer})
                if request.conversation_context:
                    yield _sse_event("context", {"conversation_context": _context_dict(request.conversation_context)})
                yield _sse_event("done", {"question": request.question})
                return

            if is_explanation_request(request.question):
                answer = (
                    explain_previous_results(request.question, previous_result, response_language)
                    if previous_result
                    else NO_PREVIOUS_RESULT_MESSAGES[response_language]
                )
                yield _sse_event("token", {"text": answer})
                if request.conversation_context:
                    yield _sse_event("context", {"conversation_context": _context_dict(request.conversation_context)})
                yield _sse_event("done", {"question": request.question})
                return

            if is_excel_export_request(request.question):
                if SCHEMA_PROVIDER == "LOCAL":
                    yield _sse_event("token", {"text": LOCAL_PREVIEW_ONLY_MESSAGES[response_language]})
                    yield _sse_event("done", {"question": request.question})
                    return
                yield from _excel_export_events(request, response_language)
                return

            planning_context = _planning_context(request.conversation_context)
            selection = resolve_pending_selection(request.question, planning_context)
            _log_query_stage(request_id, "selection_checked", context=request.conversation_context, selection=selection)
            if not selection and is_metadata_question(request.question):
                _log_query_stage(request_id, "request_classified", reason="metadata_request")
                yield _sse_event("token", {"text": clarification})
                yield _sse_event("done", {"question": request.question})
                return

            _log_query_stage(request_id, "planning_started", selection=selection)
            try:
                plan = generate_query_plan(
                    request.question,
                    response_language,
                    _planning_history(request.conversation_history),
                    planning_context,
                )
            except (SchemaNotFoundError, UnauthorizedSchemaSourceError):
                if SCHEMA_PROVIDER != "LOCAL":
                    raise
                yield _sse_event("token", {"text": LOCAL_SCHEMA_UNAVAILABLE_MESSAGES[response_language]})
                yield _sse_event("done", {"question": request.question})
                return

            if plan.clarification:
                _log_query_stage(request_id, "clarification_sent", context=plan.conversation_context,
                                 source_position=plan.clarification_source_position,
                                 reason="source_planning_clarification" if plan.clarification_source_position else None)
                if plan.conversation_context:
                    yield _sse_event("context", {"conversation_context": plan.conversation_context})
                yield _sse_event(
                    "token",
                    {"text": plan.clarification},
                )
                yield _sse_event("done", {"question": request.question})
                return

            try:
                safe_queries = [(view_id, validate_sql(sql)) for view_id, sql in plan.queries]
            except (SchemaNotFoundError, UnauthorizedSchemaSourceError):
                if SCHEMA_PROVIDER != "LOCAL":
                    raise
                yield _sse_event("token", {"text": LOCAL_SCHEMA_UNAVAILABLE_MESSAGES[response_language]})
                yield _sse_event("done", {"question": request.question})
                return
            except ValueError:
                context = _context_dict(request.conversation_context) or plan.conversation_context
                _log_query_stage(request_id, "validation_rejected", context=context, selection=selection,
                                 query_count=len(plan.queries), reason="sql_guardrail_rejection")
                if context:
                    yield _sse_event("context", {"conversation_context": context})
                yield _sse_event("token", {"text": SQL_VALIDATION_MESSAGES[response_language]})
                yield _sse_event("done", {"question": request.question})
                return

            if SCHEMA_PROVIDER == "LOCAL":
                yield _sse_event("status", {"step": "analyzing"})
                try:
                    preview = _local_sql_preview(safe_queries, response_language)
                except (SchemaNotFoundError, UnauthorizedSchemaSourceError):
                    yield _sse_event("token", {"text": LOCAL_SCHEMA_UNAVAILABLE_MESSAGES[response_language]})
                except Exception:
                    yield _sse_event("token", {"text": SQL_VALIDATION_MESSAGES[response_language]})
                else:
                    _log_query_stage(request_id, "preview_ready", context=plan.conversation_context,
                                     query_count=len(safe_queries))
                    yield _sse_event("sql_preview", preview)
                yield _sse_event("done", {"question": request.question})
                return

            yield _sse_event("status", {"step": "analyzing"})
            source_records = _source_records(safe_queries)
            for source in source_records:
                yield _sse_event("source", source)
            source_by_view = {source["view"]: source for source in source_records}

            try:
                _log_query_stage(request_id, "execution_requested", selection=selection, query_count=len(safe_queries))
                query_results = execute_sql_batch([sql for _, sql in safe_queries])
            except QueryBudgetExceeded:
                _log_query_stage(request_id, "budget_rejected", reason="combined_budget_limit")
                yield _sse_event("token", {"text": BUDGET_EXCEEDED_MESSAGES[response_language]})
                yield _sse_event("done", {"question": request.question})
                return
            yield _sse_event("status", {"step": "preparing"})
            for (view_id, _), (columns, rows) in zip(safe_queries, query_results):
                from view_catalog import get_view

                view = get_view(view_id)
                answer_parts = []
                if len(safe_queries) > 1:
                    heading = view.display_name if view else view_id
                    yield _sse_event("token", {"text": f"### {heading}\n\n"})
                if not rows:
                    answer_parts.append(NO_DATA_MESSAGES[response_language])
                    yield _sse_event("token", {"text": answer_parts[-1]})
                else:
                    for text in generate_answer_stream(request.question, columns, rows, response_language):
                        answer_parts.append(text)
                        yield _sse_event("token", {"text": text})
                if len(safe_queries) > 1:
                    yield _sse_event("token", {"text": "\n\n"})
                yield _sse_event("view_result", {
                    "view_id": view_id,
                    "display_name": view.display_name if view else view_id,
                    "markdown": "".join(answer_parts),
                    "source": source_by_view.get(view_id),
                })

            conversation_context = plan.conversation_context or {
                "pending": None,
                "last_question": request.question,
                "view_ids": [view_id for view_id, _ in safe_queries],
            }
            yield _sse_event("context", {
                "export_context": _export_context_token(
                    [{"view_id": view_id, "sql": sql} for view_id, sql in safe_queries],
                    request.question,
                ),
                "conversation_context": conversation_context,
            })
            _log_query_stage(request_id, "completed", context=conversation_context, query_count=len(safe_queries))
            yield _sse_event("done", {"question": request.question})

        except ValueError as e:
            _log_query_stage(request_id, "request_failed", reason="invalid_request")
            yield _sse_event(
                "error",
                {
                    "message": (
                        "تعذر معالجة سؤال بيانات المنصة. يرجى إعادة صياغته."
                        if response_language == "ar"
                        else "I couldn't process that NSP data question. Please try rephrasing it."
                    )
                },
            )

        except Exception as e:
            _log_query_stage(request_id, "request_failed", reason="upstream_failure")
            yield _sse_event(
                "error",
                {"message": get_error_message(e, response_language)},
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


if FRONTEND_DIST.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_DIST), html=True),
        name="frontend",
    )