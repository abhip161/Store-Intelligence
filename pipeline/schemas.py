from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


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


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class BoundingBox:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> Point:
        return Point((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def foot_point(self) -> Point:
        return Point((self.x1 + self.x2) / 2.0, self.y2)

    def as_xyxy(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)


@dataclass(frozen=True)
class Detection:
    bbox: BoundingBox
    confidence: float
    class_id: int
    class_name: str
    frame_index: int
    timestamp: datetime


@dataclass(frozen=True)
class TrackObservation:
    track_id: int
    bbox: BoundingBox
    confidence: float
    frame_index: int
    timestamp: datetime

    @property
    def center(self) -> Point:
        return self.bbox.center

    @property
    def foot_point(self) -> Point:
        return self.bbox.foot_point


@dataclass
class TrackState:
    track_id: int
    visitor_id: str
    first_seen: datetime
    last_seen: datetime
    observations: list[TrackObservation] = field(default_factory=list)
    is_staff: bool = False
    has_entered: bool = False
    has_exited: bool = False
    session_seq: int = 0

    def append(self, observation: TrackObservation) -> None:
        self.observations.append(observation)
        self.last_seen = observation.timestamp
        # Keep enough history for line crossing and re-entry heuristics without
        # retaining every frame of long clips in memory.
        if len(self.observations) > 90:
            self.observations = self.observations[-90:]

    @property
    def latest(self) -> TrackObservation | None:
        return self.observations[-1] if self.observations else None

    @property
    def previous(self) -> TrackObservation | None:
        return self.observations[-2] if len(self.observations) >= 2 else None


@dataclass(frozen=True)
class EventMetadata:
    queue_depth: int | None = None
    sku_zone: str | None = None
    session_seq: int | None = None
    track_id: int | None = None
    source: str = "pipeline"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "queue_depth": self.queue_depth,
            "sku_zone": self.sku_zone,
            "session_seq": self.session_seq,
            "track_id": self.track_id,
            "source": self.source,
        }
        payload.update(self.extra)
        return payload


@dataclass(frozen=True)
class StoreEvent:
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: str | None
    dwell_ms: int
    is_staff: bool
    confidence: float
    metadata: EventMetadata

    def to_dict(self) -> dict[str, Any]:
        timestamp = self.timestamp.astimezone(UTC).replace(microsecond=0).isoformat()
        return {
            "event_id": self.event_id,
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": self.visitor_id,
            "event_type": self.event_type.value,
            "timestamp": timestamp.replace("+00:00", "Z"),
            "zone_id": self.zone_id,
            "dwell_ms": self.dwell_ms,
            "is_staff": self.is_staff,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
            "metadata": self.metadata.to_dict(),
        }


@dataclass(frozen=True)
class CameraConfig:
    store_id: str
    camera_id: str
    video_path: str
    entry_line: tuple[Point, Point]
    inbound_side: int = 1
    start_time: datetime | None = None


@dataclass(frozen=True)
class PipelineConfig:
    model_name: str = "yolov8n.pt"
    confidence_threshold: float = 0.25
    iou_threshold: float = 0.5
    frame_stride: int = 1
    max_frames: int | None = None
    reentry_window_seconds: int = 180
    visitor_prefix: str = "VIS"
    output_jsonl: str = "data/events.jsonl"
    zone_config_path: str | None = "data/zone_config.json"
    dwell_emit_seconds: int = 30
    staff_zone_ids: tuple[str, ...] = ("STAFF_ONLY", "BACK_OFFICE")
