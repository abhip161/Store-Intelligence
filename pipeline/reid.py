from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from pipeline.schemas import TrackObservation

LOGGER = logging.getLogger(__name__)


@dataclass
class ClosedVisitor:
    visitor_id: str
    exited_at: datetime
    last_observation: TrackObservation


class VisitorIdManager:
    """Assigns stable visit-session ids and handles conservative re-entry.

    This is intentionally explainable: re-entry requires recent exit plus a
    similar position/size near the threshold. If uncertain, the pipeline creates
    a new id rather than pretending high-confidence re-identification.
    """

    def __init__(
        self,
        *,
        store_id: str,
        camera_id: str,
        visitor_prefix: str = "VIS",
        reentry_window_seconds: int = 180,
    ) -> None:
        self.store_id = store_id
        self.camera_id = camera_id
        self.visitor_prefix = visitor_prefix
        self.reentry_window = timedelta(seconds=reentry_window_seconds)
        self._next_sequence = 0
        self._track_to_visitor: dict[int, str] = {}
        self._closed_visitors: list[ClosedVisitor] = []

    def get_or_create(self, track_id: int, observation: TrackObservation) -> tuple[str, bool]:
        if track_id in self._track_to_visitor:
            return self._track_to_visitor[track_id], False

        reentry = self._match_reentry(observation)
        if reentry is not None:
            self._track_to_visitor[track_id] = reentry.visitor_id
            LOGGER.info(
                "visitor_reentry_matched",
                extra={
                    "store_id": self.store_id,
                    "camera_id": self.camera_id,
                    "visitor_id": reentry.visitor_id,
                    "track_id": track_id,
                },
            )
            return reentry.visitor_id, True

        visitor_id = self._new_visitor_id(track_id, observation.timestamp)
        self._track_to_visitor[track_id] = visitor_id
        return visitor_id, False

    def close_visitor(self, visitor_id: str, observation: TrackObservation) -> None:
        self._closed_visitors.append(
            ClosedVisitor(
                visitor_id=visitor_id,
                exited_at=observation.timestamp,
                last_observation=observation,
            )
        )
        cutoff = observation.timestamp - self.reentry_window
        self._closed_visitors = [
            visitor for visitor in self._closed_visitors if visitor.exited_at >= cutoff
        ]

    def _new_visitor_id(self, track_id: int, timestamp: datetime) -> str:
        self._next_sequence += 1
        seed = (
            f"{self.store_id}:{self.camera_id}:"
            f"{timestamp.isoformat()}:{track_id}:{self._next_sequence}"
        )
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
        return f"{self.visitor_prefix}_{digest}"

    def _match_reentry(self, observation: TrackObservation) -> ClosedVisitor | None:
        candidates: list[tuple[float, ClosedVisitor]] = []
        for visitor in self._closed_visitors:
            age = observation.timestamp - visitor.exited_at
            if age < timedelta(seconds=0) or age > self.reentry_window:
                continue

            previous = visitor.last_observation.bbox
            current = observation.bbox
            center_distance = (
                (previous.center.x - current.center.x) ** 2
                + (previous.center.y - current.center.y) ** 2
            ) ** 0.5
            size_ratio = min(previous.area, current.area) / max(previous.area, current.area, 1.0)

            # Pixel threshold is deliberately permissive because CCTV resolution
            # and camera placement differ. Size ratio keeps wrong matches down.
            if center_distance <= 180 and size_ratio >= 0.45:
                candidates.append((center_distance - (size_ratio * 25), visitor))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]
