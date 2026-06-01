from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import get_settings
from app.errors import structured_error
from app.health import HealthService
from app.models import HealthResponse
from app.repository import DatabaseUnavailable, EventRepository

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def get_health(request: Request):
    try:
        return HealthService(EventRepository(), get_settings()).health()
    except DatabaseUnavailable:
        return structured_error(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Event store is unavailable.",
            trace_id=getattr(request.state, "trace_id", None),
        )

