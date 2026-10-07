from difflib import get_close_matches
import json
import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from bigquery_database import execute_sql
from gemini_service import (
    OUT_OF_SCOPE_CLARIFICATION,
    generate_sql,
    generate_answer,
    generate_answer_stream,
    get_out_of_scope_clarification,
    translate_texts,
    translate_questions_to_arabic,
)
from sql_guardrails import validate_sql


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
        generated_sql = generate_sql(request.question, response_language)

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
            detail=(
                "تعذر معالجة الطلب. يرجى المحاولة مرة أخرى."
                if response_language == "ar"
                else "Unable to process the request."
            ),
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

            if is_metadata_question(request.question):
                yield _sse_event("token", {"text": clarification})
                yield _sse_event("done", {"question": request.question})
                return

            generated_sql = generate_sql(request.question, response_language)

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

            columns, rows = execute_sql(safe_sql)
            yield _sse_event("status", {"step": "preparing"})

            for text in generate_answer_stream(
                request.question,
                columns,
                rows,
                response_language,
            ):
                yield _sse_event("token", {"text": text})

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
                {
                    "message": (
                        "تعذر معالجة الطلب. يرجى المحاولة مرة أخرى."
                        if response_language == "ar"
                        else "Unable to process the request."
                    )
                },
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