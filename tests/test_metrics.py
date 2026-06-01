# PROMPT:
# Generate metrics tests for unique visitors, conversion rate, average dwell,
# queue depth, abandonment rate, empty store, and all-staff clips.
# CHANGES MADE:
# Used computed event batches with realistic session sequences instead of
# hardcoded API responses, and asserted zero-safe behavior for edge cases.

from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.config import get_settings

from conftest import event, ingest, visitor_session_events


def test_metrics_compute_core_store_values(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = []
    events.extend(visitor_session_events(visitor_id="VIS_BUYER", start=base, purchase=True))
    events.extend(
        visitor_session_events(
            visitor_id="VIS_BROWSER",
            start=base + timedelta(minutes=10),
        )
    )
    events.append(
        event(
            visitor_id="VIS_BROWSER",
            event_type="BILLING_QUEUE_ABANDON",
            timestamp=base + timedelta(minutes=15),
            zone_id="BILLING",
            metadata={"sku_zone": "BILLING", "session_seq": 6},
        )
    )
    ingest(client, events)

    response = client.get("/stores/STORE_BLR_002/metrics")

    assert response.status_code == 200
    metrics = response.json()
    assert metrics["unique_visitors"] == 2
    assert metrics["conversion_rate"] == 0.5
    assert metrics["queue_depth"]["current_depth"] == 0
    assert metrics["queue_depth"]["max_depth"] == 0
    assert metrics["abandonment_rate"] == 0.5
    skincare = next(zone for zone in metrics["avg_dwell_per_zone"] if zone["zone_id"] == "SKINCARE")
    assert skincare["visitor_count"] == 2
    assert skincare["avg_dwell_ms"] == 60000
    assert skincare["median_dwell_ms"] == 60000
    assert skincare["max_dwell_ms"] == 60000


def test_metrics_empty_store_returns_zeros(client):
    response = client.get("/stores/UNKNOWN_STORE/metrics")

    assert response.status_code == 200
    metrics = response.json()
    assert metrics["unique_visitors"] == 0
    assert metrics["conversion_rate"] == 0.0
    assert metrics["queue_depth"] == {
        "current_depth": 0,
        "max_depth": 0,
        "avg_wait_time": 0.0,
        "peak_wait_time": 0,
        "current": 0,
        "max": 0,
    }
    assert metrics["abandonment_rate"] == 0.0
    assert metrics["avg_dwell_per_zone"] == []


def test_metrics_compute_dwell_distribution_and_repeat_visitors(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = [
        event(visitor_id="VIS_REPEAT", event_type="ENTRY", timestamp=base),
        event(visitor_id="VIS_SINGLE", event_type="ENTRY", timestamp=base + timedelta(seconds=5)),
        event(
            visitor_id="VIS_REPEAT",
            event_type="ZONE_ENTER",
            timestamp=base + timedelta(seconds=10),
            zone_id="SKINCARE",
        ),
        event(
            visitor_id="VIS_REPEAT",
            event_type="ZONE_EXIT",
            timestamp=base + timedelta(seconds=20),
            zone_id="SKINCARE",
            dwell_ms=10_000,
        ),
        event(
            visitor_id="VIS_SINGLE",
            event_type="ZONE_ENTER",
            timestamp=base + timedelta(seconds=30),
            zone_id="SKINCARE",
        ),
        event(
            visitor_id="VIS_SINGLE",
            event_type="ZONE_EXIT",
            timestamp=base + timedelta(seconds=50),
            zone_id="SKINCARE",
            dwell_ms=20_000,
        ),
        event(
            visitor_id="VIS_REPEAT",
            event_type="ZONE_ENTER",
            timestamp=base + timedelta(seconds=60),
            zone_id="SKINCARE",
        ),
        event(
            visitor_id="VIS_REPEAT",
            event_type="ZONE_DWELL",
            timestamp=base + timedelta(seconds=75),
            zone_id="SKINCARE",
            dwell_ms=15_000,
        ),
        event(
            visitor_id="VIS_REPEAT",
            event_type="ZONE_EXIT",
            timestamp=base + timedelta(seconds=90),
            zone_id="SKINCARE",
            dwell_ms=30_000,
        ),
    ]
    ingest(client, events)

    metrics = client.get("/stores/STORE_BLR_002/metrics").json()
    skincare = next(zone for zone in metrics["avg_dwell_per_zone"] if zone["zone_id"] == "SKINCARE")

    assert skincare == {
        "zone_id": "SKINCARE",
        "avg_dwell_ms": 20000.0,
        "median_dwell_ms": 20000.0,
        "max_dwell_ms": 30000,
        "visitor_count": 2,
        "repeat_visitor_count": 1,
    }


def test_metrics_compute_queue_depth_and_wait_times_from_queue_events(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = [
        event(visitor_id="VIS_1", event_type="ENTRY", timestamp=base),
        event(visitor_id="VIS_2", event_type="ENTRY", timestamp=base),
        event(
            visitor_id="VIS_1",
            event_type="QUEUE_JOIN",
            timestamp=base + timedelta(seconds=10),
            zone_id="CASH_COUNTER",
        ),
        event(
            visitor_id="VIS_2",
            event_type="QUEUE_JOIN",
            timestamp=base + timedelta(seconds=20),
            zone_id="CASH_COUNTER",
        ),
        event(
            visitor_id="VIS_1",
            event_type="QUEUE_EXIT",
            timestamp=base + timedelta(seconds=50),
            zone_id="CASH_COUNTER",
            dwell_ms=40_000,
        ),
        event(
            visitor_id="VIS_2",
            event_type="QUEUE_EXIT",
            timestamp=base + timedelta(seconds=80),
            zone_id="CASH_COUNTER",
            dwell_ms=60_000,
        ),
        event(
            visitor_id="VIS_3",
            event_type="QUEUE_JOIN",
            timestamp=base + timedelta(seconds=90),
            zone_id="CASH_COUNTER",
        ),
    ]
    ingest(client, events)

    queue = client.get("/stores/STORE_BLR_002/metrics").json()["queue_depth"]

    assert queue["current_depth"] == 1
    assert queue["max_depth"] == 2
    assert queue["avg_wait_time"] == 50_000.0
    assert queue["peak_wait_time"] == 60_000
    assert queue["current"] == 1
    assert queue["max"] == 2


def test_cash_counter_zone_events_generate_and_persist_queue_events(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = [
        event(visitor_id="VIS_QUEUE", event_type="ENTRY", timestamp=base),
        event(
            visitor_id="VIS_QUEUE",
            event_type="ZONE_ENTER",
            timestamp=base + timedelta(seconds=5),
            zone_id="CASH_COUNTER",
        ),
        event(
            visitor_id="VIS_QUEUE",
            event_type="ZONE_EXIT",
            timestamp=base + timedelta(seconds=35),
            zone_id="CASH_COUNTER",
            dwell_ms=30_000,
        ),
    ]
    ingest(client, events)

    queue = client.get("/stores/STORE_BLR_002/metrics").json()["queue_depth"]
    connection = sqlite3.connect(get_settings().database_path)
    connection.row_factory = sqlite3.Row
    event_rows = connection.execute(
        """
        SELECT event_type, zone_id, dwell_ms
        FROM events
        WHERE visitor_id = ? AND event_type IN ('QUEUE_JOIN', 'QUEUE_EXIT')
        ORDER BY timestamp ASC
        """,
        ("VIS_QUEUE",),
    ).fetchall()
    visit = connection.execute(
        """
        SELECT visitor_id, join_time, exit_time, wait_time_ms, is_open
        FROM queue_visits
        WHERE visitor_id = ?
        """,
        ("VIS_QUEUE",),
    ).fetchone()
    connection.close()

    assert queue["current_depth"] == 0
    assert queue["max_depth"] == 1
    assert queue["avg_wait_time"] == 30_000.0
    assert [row["event_type"] for row in event_rows] == ["QUEUE_JOIN", "QUEUE_EXIT"]
    assert all(row["zone_id"] == "CASH_COUNTER" for row in event_rows)
    assert event_rows[1]["dwell_ms"] == 30_000
    assert visit["visitor_id"] == "VIS_QUEUE"
    assert visit["exit_time"] is not None
    assert visit["wait_time_ms"] == 30_000
    assert visit["is_open"] == 0


def test_metrics_exclude_all_staff_clip(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    staff_events = visitor_session_events(
        visitor_id="STAFF_001",
        start=base,
        purchase=True,
        is_staff=True,
        queue_depth=9,
    )
    ingest(client, staff_events)

    metrics = client.get("/stores/STORE_BLR_002/metrics").json()

    assert metrics["unique_visitors"] == 0
    assert metrics["conversion_rate"] == 0.0
    assert metrics["queue_depth"]["current_depth"] == 0
    assert metrics["avg_dwell_per_zone"] == []
