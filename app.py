from difflib import get_close_matches
import json
import os
from pathlib import Path

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
)
from sql_guardrails import OutOfScopeQueryError, validate_sql


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


def get_greeting_response(question: str) -> str | None:
    normalized = " ".join(question.casefold().split()).strip(".,!?;:")
    responses = {
        "hi": "Hello! How can I help you explore National Sports Platform data?",
        "hi there": "Hello! How can I help you explore National Sports Platform data?",
        "hello": "Hello! How can I help you explore National Sports Platform data?",
        "hello there": "Hello! How can I help you explore National Sports Platform data?",
        "hey": "Hello! How can I help you explore National Sports Platform data?",
        "hey there": "Hello! How can I help you explore National Sports Platform data?",
        "hiya": "Hello! How can I help you explore National Sports Platform data?",
        "howdy": "Hello! How can I help you explore National Sports Platform data?",
        "greetings": "Hello! How can I help you explore National Sports Platform data?",
        "good morning": "Good morning! How can I help you explore National Sports Platform data?",
        "good afternoon": "Good afternoon! How can I help you explore National Sports Platform data?",
        "good evening": "Good evening! How can I help you explore National Sports Platform data?",
        "good day": "Hello! How can I help you explore National Sports Platform data?",
        "good night": "Good evening! How can I help you explore National Sports Platform data?",
        "morning": "Good morning! How can I help you explore National Sports Platform data?",
        "afternoon": "Good afternoon! How can I help you explore National Sports Platform data?",
        "evening": "Good evening! How can I help you explore National Sports Platform data?",
        "what's up": "Hello! How can I help you explore National Sports Platform data?",
        "whats up": "Hello! How can I help you explore National Sports Platform data?",
        "how are you": "I'm doing well, thanks! How can I help you explore National Sports Platform data?",
        "how's it going": "I'm doing well, thanks! How can I help you explore National Sports Platform data?",
        "hows it going": "I'm doing well, thanks! How can I help you explore National Sports Platform data?",
    }
    if normalized in responses:
        return responses[normalized]

    if len(normalized) > 40 or len(normalized.split()) > 5:
        return None

    closest_match = get_close_matches(
        normalized,
        responses,
        n=1,
        cutoff=0.88,
    )
    return responses[closest_match[0]] if closest_match else None


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


@app.post("/ask")
def ask(request: QuestionRequest):

    try:
        greeting_response = get_greeting_response(request.question)
        if greeting_response:
            return {
                "question": request.question,
                "sql": None,
                "answer": greeting_response,
            }

        # 1. Natural language -> BigQuery SQL
        generated_sql = generate_sql(request.question)

        if generated_sql.upper().startswith("CLARIFICATION:"):
            clarification = generated_sql[len("CLARIFICATION:"):].strip()
            return {
                "question": request.question,
                "sql": None,
                "answer": clarification or "Could you rephrase your question?",
            }

        # 2. SQL safety validation
        safe_sql = validate_sql(generated_sql)

        # 3. Execute against BigQuery
        columns, rows = execute_sql(safe_sql)

        # 4. Database results -> grounded natural-language answer
        answer = generate_answer(
            request.question,
            columns,
            rows,
        )

        # 5. Return response to frontend
        return {
            "question": request.question,
            "sql": safe_sql,
            "answer": answer,
        }

    except OutOfScopeQueryError:
        return {
            "question": request.question,
            "sql": None,
            "answer": OUT_OF_SCOPE_CLARIFICATION,
        }

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    except Exception as e:
        print("\nERROR IN /ask:")
        print(type(e).__name__)
        print(str(e))

        raise HTTPException(
            status_code=500,
            detail="Unable to process the request.",
        )


def _sse_event(event: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


@app.post("/ask/stream")
def ask_stream(request: QuestionRequest):
    def event_stream():
        try:
            yield _sse_event("status", {"step": "understanding"})

            greeting_response = get_greeting_response(request.question)
            if greeting_response:
                yield _sse_event("token", {"text": greeting_response})
                yield _sse_event("done", {"question": request.question, "sql": None})
                return

            generated_sql = generate_sql(request.question)

            if generated_sql.upper().startswith("CLARIFICATION:"):
                clarification = generated_sql[len("CLARIFICATION:"):].strip()
                yield _sse_event(
                    "token",
                    {"text": clarification or "Could you rephrase your question?"},
                )
                yield _sse_event("done", {"question": request.question, "sql": None})
                return

            safe_sql = validate_sql(generated_sql)
            yield _sse_event("sql", {"sql": safe_sql})
            yield _sse_event("status", {"step": "analyzing"})

            columns, rows = execute_sql(safe_sql)
            yield _sse_event("status", {"step": "preparing"})

            for text in generate_answer_stream(request.question, columns, rows):
                yield _sse_event("token", {"text": text})

            yield _sse_event("done", {"question": request.question, "sql": safe_sql})

        except OutOfScopeQueryError:
            yield _sse_event("token", {"text": OUT_OF_SCOPE_CLARIFICATION})
            yield _sse_event("done", {"question": request.question, "sql": None})

        except ValueError as e:
            yield _sse_event("error", {"message": str(e)})

        except Exception as e:
            print("\nERROR IN /ask/stream:")
            print(type(e).__name__)
            print(str(e))
            yield _sse_event("error", {"message": "Unable to process the request."})

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