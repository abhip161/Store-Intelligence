from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    PURCHASE = "PURCHASE"
    QUEUE_JOIN = "QUEUE_JOIN"
    QUEUE_EXIT = "QUEUE_EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    queue_depth: int | None = Field(default=None, ge=0)
    sku_zone: str | None = None
    session_seq: int | None = Field(default=None, ge=0)


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    store_id: str = Field(min_length=1, max_length=128)
    camera_id: str = Field(min_length=1, max_length=128)
    visitor_id: str = Field(min_length=1, max_length=128)
    event_type: EventType
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int = Field(ge=0)
    is_staff: bool
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("zone_id")
    @classmethod
    def validate_zone_for_entry_exit(cls, value: str | None, info):
        event_type = info.data.get("event_type")
        if event_type in {EventType.ENTRY, EventType.EXIT} and value is not None:
            raise ValueError("zone_id must be null for ENTRY and EXIT events")
        return value


class IngestRequest(BaseModel):
    events: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class RejectedEvent(BaseModel):
    index: int
    event_id: str | None = None
    code: str
    message: str


class IngestResponse(BaseModel):
    batch_id: UUID
    accepted: int
    duplicates: int
    rejected: int
    errors: list[RejectedEvent]


class Window(BaseModel):
    start: datetime
    end: datetime


class ZoneDwellMetric(BaseModel):
    zone_id: str
    avg_dwell_ms: float
    median_dwell_ms: float
    max_dwell_ms: int
    visitor_count: int
    repeat_visitor_count: int = 0


class QueueDepthMetric(BaseModel):
    current_depth: int
    max_depth: int
    avg_wait_time: float
    peak_wait_time: int
    current: int = 0
    max: int = 0


class MetricsResponse(BaseModel):
    store_id: str
    window: Window
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: list[ZoneDwellMetric]
    queue_depth: QueueDepthMetric
    abandonment_rate: float


class FunnelStage(BaseModel):
    stage: Literal["ENTRY", "ZONE_VISIT", "CASH_COUNTER", "PURCHASE"]
    count: int
    dropoff_pct: float
    conversion_pct: float


class FunnelResponse(BaseModel):
    store_id: str
    unit: Literal["session"] = "session"
    stages: list[FunnelStage]


class HeatmapZone(BaseModel):
    zone_id: str
    visit_count: int
    avg_dwell_ms: float
    heat_score: int = Field(ge=0, le=100)
    data_confidence: Literal["LOW", "HIGH"]


class HeatmapResponse(BaseModel):
    store_id: str
    zones: list[HeatmapZone]


class AnomalySeverity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class Anomaly(BaseModel):
    type: str
    severity: AnomalySeverity
    timestamp: datetime
    details: dict[str, Any]


class AnomaliesResponse(BaseModel):
    store_id: str
    anomalies: list[Anomaly]


class TrendPoint(BaseModel):
    timestamp: datetime
    visitors: int
    purchases: int = 0
    conversion_rate: float = 0.0


class DashboardSummaryResponse(BaseModel):
    store_id: str
    window: Window
    visitor_trend: list[TrendPoint]
    conversion_trend: list[TrendPoint]


class StoreHealth(BaseModel):
    store_id: str
    last_event_timestamp: datetime | None
    feed_status: Literal["OK", "STALE_FEED", "NO_EVENTS"]
    lag_seconds: int | None


class HealthResponse(BaseModel):
    status: Literal["OK", "DEGRADED"]
    database: Literal["OK", "UNAVAILABLE"]
    stores: list[StoreHealth]
