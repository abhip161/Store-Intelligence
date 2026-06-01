from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import cv2

from pipeline.detector import YoloPersonDetector
from pipeline.event_builder import EventBuilder
from pipeline.line_crossing import CrossingDirection, EntryExitLine
from pipeline.reid import VisitorIdManager
from pipeline.schemas import (
    BoundingBox,
    Detection,
    EventType,
    StoreEvent,
    TrackObservation,
    TrackState,
)
from pipeline.zones import ZoneEngine

LOGGER = logging.getLogger(__name__)


@dataclass
class _SimpleTrack:
    track_id: int
    observation: TrackObservation
    missed_frames: int = 0


def _iou(left, right) -> float:
    x1 = max(left.x1, right.x1)
    y1 = max(left.y1, right.y1)
    x2 = min(left.x2, right.x2)
    y2 = min(left.y2, right.y2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = max(left.area + right.area - intersection, 1.0)
    return intersection / union


class SimpleByteTrackAdapter:
    """Small deterministic tracker used when raw YOLO detections are supplied.

    The production path can be replaced with Ultralytics `model.track(...,
    tracker="bytetrack.yaml")`. This adapter preserves the same downstream
    contract and keeps tests deterministic without GPU/model dependencies.
    """

    def __init__(self, *, iou_match_threshold: float = 0.3, max_missed_frames: int = 30) -> None:
        self.iou_match_threshold = iou_match_threshold
        self.max_missed_frames = max_missed_frames
        self._next_track_id = 1
        self._active: dict[int, _SimpleTrack] = {}

    def update(self, detections: list[Detection]) -> list[TrackObservation]:
        observations: list[TrackObservation] = []
        unmatched_tracks = set(self._active)

        for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
            best_track_id: int | None = None
            best_iou = 0.0
            for track_id in unmatched_tracks:
                score = _iou(self._active[track_id].observation.bbox, detection.bbox)
                if score > best_iou:
                    best_iou = score
                    best_track_id = track_id

            if best_track_id is None or best_iou < self.iou_match_threshold:
                best_track_id = self._next_track_id
                self._next_track_id += 1
            else:
                unmatched_tracks.remove(best_track_id)

            observation = TrackObservation(
                track_id=best_track_id,
                bbox=detection.bbox,
                confidence=detection.confidence,
                frame_index=detection.frame_index,
                timestamp=detection.timestamp,
            )
            self._active[best_track_id] = _SimpleTrack(best_track_id, observation)
            observations.append(observation)

        for track_id in list(unmatched_tracks):
            track = self._active[track_id]
            track.missed_frames += 1
            if track.missed_frames > self.max_missed_frames:
                del self._active[track_id]

        return observations


class UltralyticsByteTrackAdapter:
    """YOLOv8 + ByteTrack video adapter.

    Ultralytics owns the actual ByteTrack implementation through
    `tracker="bytetrack.yaml"`. This class converts its track outputs into the
    challenge pipeline's internal `TrackObservation` contract.
    """

    def __init__(
        self,
        *,
        detector: YoloPersonDetector,
        frame_stride: int = 1,
        max_frames: int | None = None,
    ) -> None:
        if frame_stride < 1:
            raise ValueError("frame_stride must be >= 1")
        if max_frames is not None and max_frames < 1:
            raise ValueError("max_frames must be >= 1 when provided")
        self.detector = detector
        self.frame_stride = frame_stride
        self.max_frames = max_frames

    def iter_video_observations(
        self,
        video_path: str | Path,
        *,
        start_time: datetime,
    ) -> Iterator[list[TrackObservation]]:
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video file does not exist: {video_path}")

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise RuntimeError(f"Unable to open video file: {video_path}")
        fps = capture.get(cv2.CAP_PROP_FPS) or 15.0
        capture.release()

        model = self.detector._load_model()
        LOGGER.info(
            "bytetrack_started",
            extra={"video_path": str(video_path), "fps": fps, "frame_stride": self.frame_stride},
        )

        try:
            stream = model.track(
                source=str(video_path),
                stream=True,
                tracker="bytetrack.yaml",
                classes=[YoloPersonDetector.PERSON_CLASS_ID],
                conf=self.detector.confidence_threshold,
                device="cpu",
                iou=self.detector.iou_threshold,
                vid_stride=self.frame_stride,
                persist=True,
                verbose=False,
            )
            for stream_index, result in enumerate(stream):
                frame_index = stream_index * self.frame_stride
                if self.max_frames is not None and frame_index >= self.max_frames:
                    break
                timestamp = start_time + timedelta(seconds=frame_index / fps)
                boxes = getattr(result, "boxes", None)
                observations: list[TrackObservation] = []
                if boxes is None or boxes.id is None:
                    yield observations
                    continue

                for box in boxes:
                    class_id = int(box.cls[0].item()) if box.cls is not None else -1
                    if class_id != YoloPersonDetector.PERSON_CLASS_ID:
                        continue
                    if box.id is None:
                        continue
                    x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
                    observations.append(
                        TrackObservation(
                            track_id=int(box.id[0].item()),
                            bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                            confidence=float(box.conf[0].item()) if box.conf is not None else 0.0,
                            frame_index=frame_index,
                            timestamp=timestamp,
                        )
                    )
                yield observations
        finally:
            LOGGER.info("bytetrack_finished", extra={"video_path": str(video_path)})


class EventTracker:
    def __init__(
        self,
        *,
        store_id: str,
        camera_id: str,
        entry_line: EntryExitLine,
        visitor_ids: VisitorIdManager,
        event_builder: EventBuilder,
        zone_engine: ZoneEngine | None = None,
        staff_zone_ids: tuple[str, ...] = ("STAFF_ONLY", "BACK_OFFICE"),
    ) -> None:
        self.store_id = store_id
        self.camera_id = camera_id
        self.entry_line = entry_line
        self.visitor_ids = visitor_ids
        self.event_builder = event_builder
        self.zone_engine = zone_engine
        self.staff_zone_ids = {zone_id.upper() for zone_id in staff_zone_ids}
        self._states: dict[int, TrackState] = {}

    def update(self, observations: list[TrackObservation]) -> list[StoreEvent]:
        events: list[StoreEvent] = []

        for observation in observations:
            visitor_id, matched_reentry = self.visitor_ids.get_or_create(
                observation.track_id, observation
            )
            state = self._states.get(observation.track_id)
            if state is None:
                state = TrackState(
                    track_id=observation.track_id,
                    visitor_id=visitor_id,
                    first_seen=observation.timestamp,
                    last_seen=observation.timestamp,
                )
                self._states[observation.track_id] = state

            state.append(observation)
            if matched_reentry and not state.has_entered:
                state.has_entered = True
                state.session_seq += 1
                events.append(
                    self.event_builder.build(
                        visitor_id=visitor_id,
                        event_type=EventType.REENTRY,
                        timestamp=observation.timestamp,
                        confidence=observation.confidence,
                        is_staff=state.is_staff,
                        session_seq=state.session_seq,
                        track_id=observation.track_id,
                    )
                )

            if self.zone_engine is not None:
                for transition in self.zone_engine.update(
                    camera_id=self.camera_id,
                    observation=observation,
                ):
                    if transition.zone_id.upper() in self.staff_zone_ids:
                        state.is_staff = True
                    state.session_seq += 1
                    events.append(
                        self.event_builder.build(
                            visitor_id=visitor_id,
                            event_type=transition.event_type,
                            timestamp=transition.timestamp,
                            confidence=observation.confidence,
                            is_staff=state.is_staff,
                            zone_id=transition.zone_id,
                            dwell_ms=transition.dwell_ms,
                            sku_zone=transition.zone_id,
                            session_seq=state.session_seq,
                            track_id=observation.track_id,
                            extra_metadata={
                                "zone_source": "zone_config",
                                "zone_event": transition.event_type.value,
                            },
                        )
                    )

            previous = state.previous
            latest = state.latest
            if previous is None or latest is None:
                continue

            crossing = self.entry_line.detect_crossing(previous.foot_point, latest.foot_point)
            if crossing is None:
                continue

            if crossing.direction == CrossingDirection.INBOUND and not state.has_entered:
                state.has_entered = True
                state.session_seq += 1
                events.append(
                    self.event_builder.build(
                        visitor_id=visitor_id,
                        event_type=EventType.ENTRY,
                        timestamp=latest.timestamp,
                        confidence=latest.confidence,
                        is_staff=state.is_staff,
                        session_seq=state.session_seq,
                        track_id=observation.track_id,
                        extra_metadata={"crossing": crossing.direction.value},
                    )
                )
            elif crossing.direction == CrossingDirection.OUTBOUND and not state.has_exited:
                state.has_exited = True
                state.session_seq += 1
                self.visitor_ids.close_visitor(visitor_id, latest)
                events.append(
                    self.event_builder.build(
                        visitor_id=visitor_id,
                        event_type=EventType.EXIT,
                        timestamp=latest.timestamp,
                        confidence=latest.confidence,
                        is_staff=state.is_staff,
                        session_seq=state.session_seq,
                        track_id=observation.track_id,
                        extra_metadata={"crossing": crossing.direction.value},
                    )
                )

        if events:
            LOGGER.info(
                "tracking_events_emitted",
                extra={
                    "store_id": self.store_id,
                    "camera_id": self.camera_id,
                    "count": len(events),
                },
            )
        return events
