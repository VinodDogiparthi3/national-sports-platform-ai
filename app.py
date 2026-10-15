from difflib import get_close_matches
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
from typing import Literal

import sqlglot
from fastapi import FastAPI, HTTPException
from google.api_core import exceptions as api_exceptions
from google.genai import errors as genai_errors
from sqlglot import exp
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from bigquery_database import execute_sql, get_export_row_count, stream_query_rows
from config import (
    EXPORT_CONTEXT_TTL_SECONDS,
    EXPORT_DOWNLOAD_TTL_SECONDS,
    EXPORT_MAX_ROWS,
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
)
from gemini_service import (
    OUT_OF_SCOPE_CLARIFICATION,
    extract_export_analysis_question,
    generate_routed_sql,
    generate_answer,
    generate_answer_stream,
    get_out_of_scope_clarification,
    translate_texts,
    translate_questions_to_arabic,
)
from sql_guardrails import validate_sql
from view_catalog import find_source


# --------------------------------------------------
# FastAPI
# --------------------------------------------------

app = FastAPI(
    title="National Sports Platform AI API",
    version="1.0.0",
)
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


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
        "object_name": first.table if first.object_type == "Table" else first.view,
        "display_name": first.display_name,
        "domain": first.domain,
        "sql": sql,
    }


NO_DATA_MESSAGES = {
    "en": "No matching data was found for this question.",
    "ar": "لم يتم العثور على بيانات مطابقة لهذا السؤال.",
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


def _export_context_token(sql: str, question: str) -> str:
    return encrypt_token({"kind": "context", "sql": sql, "question": question})


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

        new_context_sql = None
        if analysis_question:
            if is_metadata_question(analysis_question):
                yield _sse_event("token", {"text": get_out_of_scope_clarification(response_language)})
                yield done
                return

            generated_sql = generate_routed_sql(analysis_question, response_language)
            if generated_sql.upper().startswith("CLARIFICATION:"):
                clarification = generated_sql[len("CLARIFICATION:"):].strip()
                yield _sse_event(
                    "token",
                    {"text": clarification or get_out_of_scope_clarification(response_language)},
                )
                yield done
                return
            source_sql = new_context_sql = generated_sql
        elif context:
            source_sql = context["sql"]
        else:
            yield _sse_event("token", {"text": messages["no_context"]})
            yield done
            return

        try:
            export_sql = validate_sql(source_sql, row_limit=None)
        except ValueError:
            yield _sse_event("token", {"text": get_out_of_scope_clarification(response_language)})
            yield done
            return

        yield _sse_event("status", {"step": "analyzing"})
        try:
            row_count = get_export_row_count(export_sql)
        except ValueError:
            yield _sse_event("token", {"text": messages["too_large_query"]})
            yield done
            return

        if row_count == 0:
            yield _sse_event("token", {"text": messages["no_data"]})
            yield done
            return
        if row_count > EXPORT_MAX_ROWS:
            yield _sse_event(
                "token",
                {"text": messages["too_large"].format(rows=row_count, limit=EXPORT_MAX_ROWS)},
            )
            yield done
            return

        download_token = encrypt_token(
            {"kind": "download", "sql": export_sql, "language": response_language}
        )
        yield _sse_event("token", {"text": messages["ready"]})
        yield _sse_event(
            "export",
            {"download_url": f"/exports/{download_token}", "row_count": row_count},
        )
        if new_context_sql:
            yield _sse_event(
                "context",
                {"export_context": _export_context_token(new_context_sql, analysis_question)},
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
        "status": "healthy"
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
        sql = validate_sql(payload["sql"], row_limit=None)
        columns, rows = stream_query_rows(sql)
        path, _ = write_workbook(
            _localized_headers(columns, language),
            rows,
            language=language,
            max_rows=EXPORT_MAX_ROWS,
        )
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

    try:
        response_language = get_response_language(request.question, request.language)
        clarification = get_out_of_scope_clarification(response_language)
        greeting_response = get_greeting_response(request.question, response_language)
        if greeting_response:
            return {
                "question": request.question,
                "answer": greeting_response,
            }

        if is_metadata_question(request.question):
            return {
                "question": request.question,
                "answer": clarification,
            }

        # 1. Natural language -> BigQuery SQL
        generated_sql = generate_routed_sql(request.question, response_language)

        if generated_sql.upper().startswith("CLARIFICATION:"):
            clarification = generated_sql[len("CLARIFICATION:"):].strip()
            return {
                "question": request.question,
                "answer": clarification or (
                    "يرجى إعادة صياغة السؤال." if response_language == "ar"
                    else "Could you rephrase your question?"
                ),
            }

        # 2. SQL safety validation
        try:
            safe_sql = validate_sql(generated_sql)
        except ValueError:
            return {
                "question": request.question,
                "answer": clarification,
            }

        # 3. Execute against BigQuery
        columns, rows = execute_sql(safe_sql)
        source = get_source_info(safe_sql)

        if not rows:
            return {
                "question": request.question,
                "answer": NO_DATA_MESSAGES[response_language],
                **({"source": source} if source else {}),
            }

        # 4. Database results -> grounded natural-language answer
        answer = generate_answer(
            request.question,
            columns,
            rows,
            response_language,
        )

        # 5. Return response to frontend
        return {
            "question": request.question,
            "answer": answer,
            **({"source": source} if source else {}),
        }

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=(
                "تعذر معالجة سؤال بيانات المنصة. يرجى إعادة صياغته."
                if response_language == "ar"
                else "I couldn't process that NSP data question. Please try rephrasing it."
            ),
        )

    except Exception as e:
        print("\nERROR IN /ask:")
        print(type(e).__name__)
        print(str(e))

        raise HTTPException(
            status_code=500,
            detail=get_error_message(e, response_language),
        )


def _sse_event(event: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


@app.post("/ask/stream")
def ask_stream(request: QuestionRequest):
    def event_stream():
        try:
            response_language = get_response_language(request.question, request.language)
            clarification = get_out_of_scope_clarification(response_language)
            yield _sse_event("status", {"step": "understanding"})

            greeting_response = get_greeting_response(request.question, response_language)
            if greeting_response:
                yield _sse_event("token", {"text": greeting_response})
                yield _sse_event("done", {"question": request.question})
                return

            if is_excel_export_request(request.question):
                yield from _excel_export_events(request, response_language)
                return

            if is_metadata_question(request.question):
                yield _sse_event("token", {"text": clarification})
                yield _sse_event("done", {"question": request.question})
                return

            generated_sql = generate_routed_sql(request.question, response_language)

            if generated_sql.upper().startswith("CLARIFICATION:"):
                clarification = generated_sql[len("CLARIFICATION:"):].strip()
                yield _sse_event(
                    "token",
                    {
                        "text": clarification or (
                            "يرجى إعادة صياغة السؤال." if response_language == "ar"
                            else "Could you rephrase your question?"
                        )
                    },
                )
                yield _sse_event("done", {"question": request.question})
                return

            try:
                safe_sql = validate_sql(generated_sql)
            except ValueError:
                yield _sse_event("token", {"text": clarification})
                yield _sse_event("done", {"question": request.question})
                return

            yield _sse_event("status", {"step": "analyzing"})
            source = get_source_info(safe_sql)
            if source:
                yield _sse_event("source", source)

            columns, rows = execute_sql(safe_sql)
            if not rows:
                yield _sse_event("token", {"text": NO_DATA_MESSAGES[response_language]})
                yield _sse_event("done", {"question": request.question})
                return
            yield _sse_event("status", {"step": "preparing"})

            for text in generate_answer_stream(
                request.question,
                columns,
                rows,
                response_language,
            ):
                yield _sse_event("token", {"text": text})

            yield _sse_event(
                "context",
                {"export_context": _export_context_token(generated_sql, request.question)},
            )
            yield _sse_event("done", {"question": request.question})

        except ValueError as e:
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
            print("\nERROR IN /ask/stream:")
            print(type(e).__name__)
            print(str(e))
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