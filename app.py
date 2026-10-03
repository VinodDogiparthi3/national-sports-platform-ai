from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from bigquery_database import execute_sql
from gemini_service import generate_sql, generate_answer
from sql_guardrails import validate_sql


# --------------------------------------------------
# FastAPI
# --------------------------------------------------

app = FastAPI(
    title="National Sports Platform AI API",
    version="1.0.0",
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


# --------------------------------------------------
# API Endpoints
# --------------------------------------------------

@app.get("/")
def root():
    return {
        "message": "National Sports Platform AI API is running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


@app.post("/ask")
def ask(request: QuestionRequest):

    try:
        # 1. Natural language -> BigQuery SQL
        generated_sql = generate_sql(request.question)

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