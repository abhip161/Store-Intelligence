from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pipeline.schemas import EventType, Point, TrackObservation

LOGGER = logging.getLogger(__name__)


REQUIRED_ZONE_IDS = {
    "ENTRY",
    "CASH_COUNTER",
    "FRAGRANCE_ISLAND",
    "MAKEUP_UNIT",
    "MINIMALIST",
    "AQUALOGICA",
    "PILGRIM",
    "DERMDOC",
    "D_AND_K",
    "MAYBELLINE",
    "FACES",
    "LAKME",
    "SWISS_PLUS",
    "LOREAL",
    "ALPS",
    "BEAUTY_ESSENTIAL",
}


@dataclass(frozen=True)
class ZoneDefinition:
    zone_id: str
    polygon: tuple[Point, ...]

    def contains(self, point: Point) -> bool:
        return point_in_polygon(point, self.polygon)


@dataclass(frozen=True)
class CameraZoneConfig:
    camera_id: str
    frame_width: float
    frame_height: float
    zones: tuple[ZoneDefinition, ...]


@dataclass(frozen=True)
class StoreZoneConfig:
    store_id: str
    cameras: dict[str, CameraZoneConfig]
    source: str
    version: str
    layout_path: Path | None = None
    layout_media_sha256: str | None = None


@dataclass
class TrackZoneState:
    zone_id: str | None = None
    entered_at: datetime | None = None
    last_dwell_emitted_at: datetime | None = None


@dataclass(frozen=True)
class ZoneTransition:
    event_type: EventType
    zone_id: str
    timestamp: datetime
    dwell_ms: int = 0


def point_in_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
    """Ray-casting point-in-polygon with boundary treated as inside."""

    if len(polygon) < 3:
        return False

    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _point_on_segment(point, previous, current):
            return True
        crosses_y = (current.y > point.y) != (previous.y > point.y)
        if crosses_y:
            slope_x = (previous.x - current.x) * (point.y - current.y) / (
                previous.y - current.y
            ) + current.x
            if point.x < slope_x:
                inside = not inside
        previous = current
    return inside


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    cross = (point.y - start.y) * (end.x - start.x) - (point.x - start.x) * (end.y - start.y)
    if abs(cross) > 1e-6:
        return False
    min_x, max_x = sorted((start.x, end.x))
    min_y, max_y = sorted((start.y, end.y))
    return min_x - 1e-6 <= point.x <= max_x + 1e-6 and min_y - 1e-6 <= point.y <= max_y + 1e-6


def load_zone_config(path: str | Path, *, store_id: str) -> StoreZoneConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Zone config does not exist: {config_path}")

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    layout_path, layout_media_sha256 = _layout_source(payload, config_path)
    stores = payload.get("stores", {})
    store_payload = stores.get(store_id)
    if not isinstance(store_payload, dict):
        raise ValueError(f"Store {store_id!r} not found in zone config {config_path}")

    cameras: dict[str, CameraZoneConfig] = {}
    configured_zone_ids: set[str] = set()
    for camera_id, camera_payload in store_payload.get("cameras", {}).items():
        frame_width = float(camera_payload["frame_width"])
        frame_height = float(camera_payload["frame_height"])
        _validate_frame(camera_id, frame_width, frame_height)
        zones: list[ZoneDefinition] = []
        camera_zone_ids: set[str] = set()
        for zone_payload in camera_payload.get("zones", []):
            zone_id = str(zone_payload["zone_id"])
            if zone_id in camera_zone_ids:
                raise ValueError(f"Duplicate zone {zone_id!r} in camera {camera_id!r}")
            camera_zone_ids.add(zone_id)
            configured_zone_ids.add(zone_id)
            polygon = tuple(
                _scale_point(Point(float(x), float(y)), frame_width, frame_height)
                for x, y in zone_payload["polygon"]
            )
            _validate_polygon(camera_id, zone_id, polygon, frame_width, frame_height)
            zones.append(ZoneDefinition(zone_id=zone_id, polygon=polygon))
        cameras[camera_id] = CameraZoneConfig(
            camera_id=camera_id,
            frame_width=frame_width,
            frame_height=frame_height,
            zones=tuple(zones),
        )

    missing = REQUIRED_ZONE_IDS - configured_zone_ids
    if missing:
        raise ValueError(f"Zone config missing required zones: {sorted(missing)}")

    return StoreZoneConfig(
        store_id=store_id,
        cameras=cameras,
        source=str(payload.get("source", config_path)),
        version=str(payload.get("version", "unknown")),
        layout_path=layout_path,
        layout_media_sha256=layout_media_sha256,
    )


def _layout_source(payload: dict, config_path: Path) -> tuple[Path | None, str | None]:
    source = payload.get("layout_source")
    if source is None:
        return None, None
    if not isinstance(source, dict):
        raise ValueError("layout_source must be an object when provided")

    raw_path = source.get("path")
    layout_path = None
    if raw_path:
        layout_path = Path(str(raw_path))
        if not layout_path.is_absolute():
            layout_path = config_path.parent.parent / layout_path
        if not layout_path.exists():
            raise FileNotFoundError(f"Layout source does not exist: {layout_path}")

    media_sha256 = source.get("embedded_media_sha256")
    if media_sha256 is not None and not isinstance(media_sha256, str):
        raise ValueError("layout_source.embedded_media_sha256 must be a string")
    return layout_path, media_sha256


def _scale_point(point: Point, frame_width: float, frame_height: float) -> Point:
    if 0.0 <= point.x <= 1.0 and 0.0 <= point.y <= 1.0:
        return Point(point.x * frame_width, point.y * frame_height)
    return point


def _validate_frame(camera_id: str, frame_width: float, frame_height: float) -> None:
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError(f"Camera {camera_id!r} frame dimensions must be positive")
    if not math.isfinite(frame_width) or not math.isfinite(frame_height):
        raise ValueError(f"Camera {camera_id!r} frame dimensions must be finite")


def _validate_polygon(
    camera_id: str,
    zone_id: str,
    polygon: tuple[Point, ...],
    frame_width: float,
    frame_height: float,
) -> None:
    if len(polygon) < 3:
        raise ValueError(f"Zone {zone_id!r} in camera {camera_id!r} must have >= 3 points")
    for point in polygon:
        if not math.isfinite(point.x) or not math.isfinite(point.y):
            raise ValueError(f"Zone {zone_id!r} in camera {camera_id!r} has non-finite point")
        if point.x < 0 or point.x > frame_width or point.y < 0 or point.y > frame_height:
            raise ValueError(f"Zone {zone_id!r} in camera {camera_id!r} has point outside frame")
    if _polygon_area(polygon) <= 1e-6:
        raise ValueError(f"Zone {zone_id!r} in camera {camera_id!r} has zero-area polygon")


def _polygon_area(polygon: tuple[Point, ...]) -> float:
    area = 0.0
    previous = polygon[-1]
    for current in polygon:
        area += (previous.x * current.y) - (current.x * previous.y)
        previous = current
    return abs(area) / 2.0


class ZoneEngine:
    def __init__(self, config: StoreZoneConfig, *, dwell_interval_ms: int = 30_000) -> None:
        self.config = config
        self.dwell_interval_ms = dwell_interval_ms
        self._track_states: dict[tuple[str, int], TrackZoneState] = {}

    def zone_for_observation(self, camera_id: str, observation: TrackObservation) -> str | None:
        camera = self.config.cameras.get(camera_id)
        if camera is None:
            return None
        point = observation.foot_point
        for zone in camera.zones:
            if zone.contains(point):
                return zone.zone_id
        return None

    def update(self, *, camera_id: str, observation: TrackObservation) -> list[ZoneTransition]:
        state_key = (camera_id, observation.track_id)
        state = self._track_states.setdefault(state_key, TrackZoneState())
        detected_zone = self.zone_for_observation(camera_id, observation)
        timestamp = observation.timestamp
        transitions: list[ZoneTransition] = []

        if detected_zone != state.zone_id:
            if state.zone_id is not None and state.entered_at is not None:
                transitions.append(
                    ZoneTransition(
                        event_type=EventType.ZONE_EXIT,
                        zone_id=state.zone_id,
                        timestamp=timestamp,
                        dwell_ms=_elapsed_ms(state.entered_at, timestamp),
                    )
                )
            if detected_zone is not None:
                transitions.append(
                    ZoneTransition(
                        event_type=EventType.ZONE_ENTER,
                        zone_id=detected_zone,
                        timestamp=timestamp,
                    )
                )
                state.entered_at = timestamp
                state.last_dwell_emitted_at = timestamp
            else:
                state.entered_at = None
                state.last_dwell_emitted_at = None
            state.zone_id = detected_zone
            return transitions

        if state.zone_id is not None and state.entered_at is not None:
            last_dwell = state.last_dwell_emitted_at or state.entered_at
            elapsed_since_dwell = _elapsed_ms(last_dwell, timestamp)
            if elapsed_since_dwell >= self.dwell_interval_ms:
                transitions.append(
                    ZoneTransition(
                        event_type=EventType.ZONE_DWELL,
                        zone_id=state.zone_id,
                        timestamp=timestamp,
                        dwell_ms=_elapsed_ms(state.entered_at, timestamp),
                    )
                )
                state.last_dwell_emitted_at = timestamp

        return transitions


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))
