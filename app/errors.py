from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse


def structured_error(
    *,
    status_code: int,
    code: str,
    message: str,
    trace_id: str | None = None,
    details: object | None = None,
) -> ORJSONResponse:
    return ORJSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "trace_id": trace_id,
                "details": details,
            }
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> ORJSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    return structured_error(
        status_code=500,
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected server error occurred.",
        trace_id=trace_id,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> ORJSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    return structured_error(
        status_code=422,
        code="REQUEST_VALIDATION_ERROR",
        message="Request payload or parameters failed validation.",
        trace_id=trace_id,
        details=exc.errors(),
    )
