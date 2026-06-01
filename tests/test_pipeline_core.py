# PROMPT:
# Generate deterministic unit tests for the detection pipeline primitives without
# requiring YOLO weights, GPU access, or video files.
# CHANGES MADE:
# Tested line crossing, event schema generation, simple tracking, and re-entry
# matching because these map directly to high-score edge cases.

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pipeline.event_builder import EventBuilder
from pipeline.line_crossing import CrossingDirection, EntryExitLine
from pipeline.reid import VisitorIdManager
from pipeline.schemas import BoundingBox, Detection, EventType, Point, TrackObservation
from pipeline.tracker import EventTracker, SimpleByteTrackAdapter
from pipeline.zones import ZoneTransition


def test_line_crossing_detects_inbound_and_outbound():
    line = EntryExitLine(Point(0, 10), Point(10, 10), inbound_side=1)

    inbound = line.detect_crossing(Point(5, 5), Point(5, 15))
    outbound = line.detect_crossing(Point(5, 15), Point(5, 5))

    assert inbound is not None
    assert inbound.direction == CrossingDirection.INBOUND
    assert outbound is not None
    assert outbound.direction == CrossingDirection.OUTBOUND


def test_event_builder_outputs_challenge_schema():
    timestamp = datetime(2026, 3, 3, 14, 22, 10, tzinfo=UTC)
    builder = EventBuilder(store_id="STORE_BLR_002", camera_id="CAM_ENTRY_01")

    event = builder.build(
        visitor_id="VIS_TEST",
        event_type=EventType.ENTRY,
        timestamp=timestamp,
        confidence=0.91321,
        is_staff=False,
        session_seq=1,
        track_id=7,
    ).to_dict()

    assert event["store_id"] == "STORE_BLR_002"
    assert event["event_type"] == "ENTRY"
    assert event["timestamp"] == "2026-03-03T14:22:10Z"
    assert event["confidence"] == 0.9132
    assert event["metadata"]["session_seq"] == 1
    assert event["metadata"]["track_id"] == 7


def test_simple_tracker_keeps_group_members_separate():
    tracker = SimpleByteTrackAdapter(iou_match_threshold=0.3)
    timestamp = datetime.now(UTC)
    detections = [
        Detection(
            bbox=BoundingBox(0, 0, 50, 100),
            confidence=0.9,
            class_id=0,
            class_name="person",
            frame_index=1,
            timestamp=timestamp,
        ),
        Detection(
            bbox=BoundingBox(80, 0, 130, 100),
            confidence=0.88,
            class_id=0,
            class_name="person",
            frame_index=1,
            timestamp=timestamp,
        ),
    ]

    observations = tracker.update(detections)

    assert len(observations) == 2
    assert len({observation.track_id for observation in observations}) == 2


def test_visitor_id_manager_matches_reentry_conservatively():
    manager = VisitorIdManager(
        store_id="STORE_BLR_002",
        camera_id="CAM_ENTRY_01",
        reentry_window_seconds=180,
    )
    timestamp = datetime.now(UTC)
    first_observation = TrackObservation(
        track_id=1,
        bbox=BoundingBox(100, 100, 180, 260),
        confidence=0.9,
        frame_index=10,
        timestamp=timestamp,
    )
    visitor_id, is_reentry = manager.get_or_create(1, first_observation)
    manager.close_visitor(visitor_id, first_observation)

    second_observation = TrackObservation(
        track_id=2,
        bbox=BoundingBox(110, 105, 190, 265),
        confidence=0.87,
        frame_index=100,
        timestamp=timestamp + timedelta(seconds=60),
    )
    matched_visitor_id, matched_reentry = manager.get_or_create(2, second_observation)

    assert is_reentry is False
    assert matched_visitor_id == visitor_id
    assert matched_reentry is True


class _StaffZoneEngine:
    def update(self, *, camera_id, observation):
        return [
            ZoneTransition(
                event_type=EventType.ZONE_ENTER,
                zone_id="STAFF_ONLY",
                timestamp=observation.timestamp,
            )
        ]


def test_event_tracker_marks_staff_from_configured_staff_zone():
    timestamp = datetime.now(UTC)
    tracker = EventTracker(
        store_id="STORE_BLR_002",
        camera_id="CAM_STAFF",
        entry_line=EntryExitLine(Point(0, 50), Point(100, 50)),
        visitor_ids=VisitorIdManager(store_id="STORE_BLR_002", camera_id="CAM_STAFF"),
        event_builder=EventBuilder(store_id="STORE_BLR_002", camera_id="CAM_STAFF"),
        zone_engine=_StaffZoneEngine(),
        staff_zone_ids=("STAFF_ONLY",),
    )

    events = tracker.update(
        [
            TrackObservation(
                track_id=9,
                bbox=BoundingBox(10, 10, 30, 40),
                confidence=0.88,
                frame_index=1,
                timestamp=timestamp,
            )
        ]
    )

    assert len(events) == 1
    assert events[0].event_type == EventType.ZONE_ENTER
    assert events[0].zone_id == "STAFF_ONLY"
    assert events[0].is_staff is True
