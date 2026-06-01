from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

from app.anomalies import AnomalyService
from app.dashboard_summary import DashboardSummaryService
from app.errors import structured_error
from app.funnel import FunnelService
from app.heatmap import HeatmapService
from app.metrics import MetricsService
from app.models import (
    AnomaliesResponse,
    DashboardSummaryResponse,
    FunnelResponse,
    HeatmapResponse,
    MetricsResponse,
)
from app.repository import DatabaseUnavailable, EventRepository

router = APIRouter(prefix="/stores", tags=["stores"])


@router.get("/{store_id}/metrics", response_model=MetricsResponse)
def get_metrics(
    store_id: str,
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
):
    try:
        return MetricsService(EventRepository()).metrics_for_store(
            store_id,
            start=start,
            end=end,
        )
    except DatabaseUnavailable:
        return _db_error(request)


@router.get("/{store_id}/funnel", response_model=FunnelResponse)
def get_funnel(
    store_id: str,
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
):
    try:
        return FunnelService(EventRepository()).funnel_for_store(
            store_id,
            start=start,
            end=end,
        )
    except DatabaseUnavailable:
        return _db_error(request)


@router.get("/{store_id}/heatmap", response_model=HeatmapResponse)
def get_heatmap(
    store_id: str,
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
):
    try:
        return HeatmapService(EventRepository()).heatmap_for_store(
            store_id,
            start=start,
            end=end,
        )
    except DatabaseUnavailable:
        return _db_error(request)


@router.get("/{store_id}/anomalies", response_model=AnomaliesResponse)
def get_anomalies(
    store_id: str,
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
):
    try:
        return AnomalyService(EventRepository()).anomalies_for_store(
            store_id,
            start=start,
            end=end,
        )
    except DatabaseUnavailable:
        return _db_error(request)


@router.get("/{store_id}/dashboard", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    store_id: str,
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
):
    try:
        return DashboardSummaryService(EventRepository()).summary_for_store(
            store_id,
            start=start,
            end=end,
        )
    except DatabaseUnavailable:
        return _db_error(request)


def _db_error(request: Request):
    return structured_error(
        status_code=503,
        code="DATABASE_UNAVAILABLE",
        message="Event store is unavailable.",
        trace_id=getattr(request.state, "trace_id", None),
    )
