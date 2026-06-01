# PROMPT:
# Generate unit tests for a production-grade configurable zone analytics engine
# that uses polygon zones derived from a visual store-layout source of truth.
# CHANGES MADE:
# Added deterministic tests for point-in-polygon, config loading, zone transitions,
# dwell emission, and SQLite zone visit persistence through the ingest API.

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from pipeline.schemas import BoundingBox, Point, TrackObservation
from pipeline.zones import ZoneEngine, load_zone_config, point_in_polygon

from conftest import event, ingest


def test_point_in_polygon_counts_boundary_as_inside():
    polygon = (
        Point(0, 0),
        Point(10, 0),
        Point(10, 10),
        Point(0, 10),
    )

    assert point_in_polygon(Point(5, 5), polygon) is True
    assert point_in_polygon(Point(0, 5), polygon) is True
    assert point_in_polygon(Point(15, 5), polygon) is False


def test_zone_config_loads_all_required_visual_layout_zones():
    config = load_zone_config("data/zone_config.json", store_id="STORE_BLR_002")

    configured = {
        zone.zone_id
        for camera in config.cameras.values()
        for zone in camera.zones
    }

    assert "ENTRY" in configured
    assert "CASH_COUNTER" in configured
    assert "BEAUTY_ESSENTIAL" in configured
    assert config.source == "data/store_layout.xlsx embedded floor-plan images"
    assert config.layout_path is not None
    assert config.layout_path.name == "store_layout.xlsx"
    assert config.layout_media_sha256 == (
        "4c3f1fb7b2d93a968fdbbae4858ee4b0ad9e8929013e629e4e01b9c84ff03078"
    )


def test_zone_config_rejects_invalid_polygons(tmp_path):
    zone_ids = [
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
    ]
    zones = [
        {"zone_id": zone_id, "polygon": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]]}
        for zone_id in zone_ids
    ]
    zones[0] = {"zone_id": "ENTRY", "polygon": [[0.1, 0.1], [0.1, 0.1], [0.1, 0.1]]}
    config_path = tmp_path / "zone_config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "test",
                "stores": {
                    "STORE_BLR_002": {
                        "cameras": {
                            "CAM_TEST": {
                                "frame_width": 100,
                                "frame_height": 100,
                                "zones": zones,
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    try:
        load_zone_config(config_path, store_id="STORE_BLR_002")
    except ValueError as exc:
        assert "zero-area polygon" in str(exc)
    else:
        raise AssertionError("invalid zone config should have failed")


def test_zone_engine_emits_enter_dwell_and_exit():
    config = load_zone_config("data/zone_config.json", store_id="STORE_BLR_002")
    engine = ZoneEngine(config, dwell_interval_ms=30_000)
    base = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)

    enter_obs = TrackObservation(
        track_id=7,
        bbox=BoundingBox(100, 500, 200, 800),
        confidence=0.9,
        frame_index=1,
        timestamp=base,
    )
    dwell_obs = TrackObservation(
        track_id=7,
        bbox=BoundingBox(100, 500, 200, 800),
        confidence=0.9,
        frame_index=900,
        timestamp=base + timedelta(seconds=31),
    )
    exit_obs = TrackObservation(
        track_id=7,
        bbox=BoundingBox(800, 100, 900, 250),
        confidence=0.9,
        frame_index=1200,
        timestamp=base + timedelta(seconds=45),
    )

    enter_events = engine.update(camera_id="CAM_1", observation=enter_obs)
    dwell_events = engine.update(camera_id="CAM_1", observation=dwell_obs)
    exit_events = engine.update(camera_id="CAM_1", observation=exit_obs)

    assert [event.event_type.value for event in enter_events] == ["ZONE_ENTER"]
    assert enter_events[0].zone_id == "ENTRY"
    assert [event.event_type.value for event in dwell_events] == ["ZONE_DWELL"]
    assert dwell_events[0].dwell_ms == 31_000
    assert [event.event_type.value for event in exit_events] == ["ZONE_EXIT"]
    assert exit_events[0].dwell_ms == 45_000


def test_zone_engine_switches_between_configured_zones_without_pipeline_branching():
    config = load_zone_config("data/zone_config.json", store_id="STORE_BLR_002")
    engine = ZoneEngine(config, dwell_interval_ms=30_000)
    base = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)

    entry_obs = TrackObservation(
        track_id=17,
        bbox=BoundingBox(100, 500, 200, 800),
        confidence=0.9,
        frame_index=1,
        timestamp=base,
    )
    beauty_obs = TrackObservation(
        track_id=17,
        bbox=BoundingBox(900, 500, 1_000, 800),
        confidence=0.9,
        frame_index=100,
        timestamp=base + timedelta(seconds=12),
    )

    assert engine.update(camera_id="CAM_1", observation=entry_obs)[0].zone_id == "ENTRY"
    transitions = engine.update(camera_id="CAM_1", observation=beauty_obs)

    assert [event.event_type.value for event in transitions] == ["ZONE_EXIT", "ZONE_ENTER"]
    assert transitions[0].zone_id == "ENTRY"
    assert transitions[0].dwell_ms == 12_000
    assert transitions[1].zone_id == "BEAUTY_ESSENTIAL"


def test_zone_visits_are_persisted_from_ingested_zone_events(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = [
        event(
            visitor_id="VIS_ZONE",
            camera_id="CAM_1",
            event_type="ZONE_ENTER",
            timestamp=base,
            zone_id="ENTRY",
            metadata={"sku_zone": "ENTRY", "session_seq": 1},
        ),
        event(
            visitor_id="VIS_ZONE",
            camera_id="CAM_1",
            event_type="ZONE_DWELL",
            timestamp=base + timedelta(seconds=30),
            zone_id="ENTRY",
            dwell_ms=30_000,
            metadata={"sku_zone": "ENTRY", "session_seq": 2},
        ),
        event(
            visitor_id="VIS_ZONE",
            camera_id="CAM_1",
            event_type="ZONE_EXIT",
            timestamp=base + timedelta(seconds=45),
            zone_id="ENTRY",
            dwell_ms=45_000,
            metadata={"sku_zone": "ENTRY", "session_seq": 3},
        ),
    ]

    ingest(client, events)

    db_path = get_settings().database_path
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT visitor_id, zone_id, enter_time, exit_time, dwell_ms, is_open
        FROM zone_visits
        WHERE visitor_id = ? AND zone_id = ?
        """,
        ("VIS_ZONE", "ENTRY"),
    ).fetchone()
    connection.close()

    assert row is not None
    assert row["visitor_id"] == "VIS_ZONE"
    assert row["zone_id"] == "ENTRY"
    assert row["exit_time"] is not None
    assert row["dwell_ms"] == 45_000
    assert row["is_open"] == 0
