import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, BeforeValidator

from uc2.models import AnalysisRequest, ApplicationAnalysis, SummaryRequest, SummaryResponse, Timestamp
from uc2.repository import WorkflowRepository
from uc2.security import VerifiedPrincipal, get_repository, require_uc2_access
from uc2.service import analyze, summarize


def iso_timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value,
    ):
        raise ValueError("Use an ISO-8601 timestamp with an explicit timezone.")
    return value


class SummaryQuery(SummaryRequest):
    as_of: Annotated[Timestamp, BeforeValidator(iso_timestamp)]


class AnalysisQuery(AnalysisRequest):
    as_of: Annotated[Timestamp, BeforeValidator(iso_timestamp)]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    data_mode: Literal["synthetic"] = "synthetic"
    error: ErrorDetail


ERRORS = {
    401: ("authentication_required", "Valid bearer authentication is required."),
    403: ("permission_denied", "UC2 read permission is required."),
    404: ("application_not_found", "Synthetic application is unavailable at this cutoff."),
    422: ("invalid_request", "Provide valid required path and query parameters."),
    500: ("analysis_unavailable", "Synthetic analysis could not be completed."),
}


def error_response(status_code: int) -> JSONResponse:
    code, message = ERRORS[status_code]
    headers = {"Cache-Control": "no-store"}
    if status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=status_code, headers=headers,
        content=ErrorResponse(error=ErrorDetail(code=code, message=message)).model_dump(),
    )


class SyntheticRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await handler(request)
            except RequestValidationError:
                return error_response(422)
            except HTTPException as error:
                return error_response(error.status_code if error.status_code in ERRORS else 500)
            except Exception:
                return error_response(500)
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


def unique_query_parameters(
    request: Request,
    principal: Annotated[VerifiedPrincipal, Depends(require_uc2_access)],
) -> None:
    names = [name for name, _ in request.query_params.multi_items()]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=422, detail="Duplicate query parameters.")


router = APIRouter(
    prefix="/api/uc2", tags=["UC2 synthetic"], route_class=SyntheticRoute,
    dependencies=[Depends(unique_query_parameters)],
    responses={status: {"model": ErrorResponse} for status in ERRORS},
)


@router.get("/license-delays", response_model=SummaryResponse)
def license_delays(
    query: Annotated[SummaryQuery, Query()],
    repository: Annotated[WorkflowRepository, Depends(get_repository)],
) -> SummaryResponse:
    return summarize(query, repository)


@router.get("/applications/{application_id}/analysis", response_model=ApplicationAnalysis)
def application_analysis(
    application_id: Annotated[str, Path(min_length=1, max_length=200, pattern=r"^\S+$")],
    query: Annotated[AnalysisQuery, Query()],
    repository: Annotated[WorkflowRepository, Depends(get_repository)],
) -> ApplicationAnalysis:
    try:
        return analyze(application_id, query.as_of, repository)
    except KeyError as error:
        if error.args == (application_id,):
            raise HTTPException(status_code=404, detail="Application not found.") from None
        raise