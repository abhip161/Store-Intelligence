from __future__ import annotations

from fastapi import APIRouter, Request

from app.errors import structured_error
from app.ingestion import IngestionService
from app.models import IngestRequest, IngestResponse
from app.repository import DatabaseUnavailable, EventRepository

router = APIRouter(prefix="/events", tags=["events"])


@router.post("/ingest", response_model=IngestResponse)
def ingest_events(payload: IngestRequest, request: Request):
    service = IngestionService(EventRepository())
    try:
        return service.ingest(payload)
    except DatabaseUnavailable:
        return structured_error(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Event store is unavailable.",
            trace_id=getattr(request.state, "trace_id", None),
        )

