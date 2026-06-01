# PROMPT:
# Generate production anomaly detection tests for high dwell, queue congestion,
# traffic spikes, reentry spikes, low conversion, and POS mismatch anomalies.
# CHANGES MADE:
# Replaced prose-oriented legacy anomaly tests with compact response-contract
# checks over actual persisted events and POS rows.

from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.config import get_settings
from app.repository import iso

from conftest import event, ingest


def _anomalies_by_type(payload):
    return {item["type"]: item for item in payload["anomalies"]}


def _insert_pos(transaction_id: str, timestamp, *, matched_visitor_id: str | None = None):
    connection = sqlite3.connect(get_settings().database_path)
    connection.execute(
        """
        INSERT INTO pos_transactions (
            transaction_id, store_id, timestamp, basket_value_inr, basket_value,
            product, brand, salesperson, matched_visitor_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            "STORE_BLR_002",
            iso(timestamp),
            299.0,
            299.0,
            "Lipstick",
            "Lakme",
            "Associate",
            matched_visitor_id,
        ),
    )
    connection.commit()
    connection.close()


def test_anomalies_return_requested_contract_for_high_dwell(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    ingest(
        client,
        [
            event(visitor_id="VIS_DWELL", event_type="ENTRY", timestamp=base),
            event(
                visitor_id="VIS_DWELL",
                event_type="ZONE_ENTER",
                timestamp=base + timedelta(minutes=1),
                zone_id="DERMDOC",
            ),
            event(
                visitor_id="VIS_DWELL",
                event_type="ZONE_EXIT",
                timestamp=base + timedelta(minutes=12),
                zone_id="DERMDOC",
                dwell_ms=11 * 60 * 1000,
            ),
        ],
    )

    anomalies = _anomalies_by_type(client.get("/stores/STORE_BLR_002/anomalies").json())

    anomaly = anomalies["HIGH_DWELL"]
    assert set(anomaly) == {"type", "severity", "timestamp", "details"}
    assert anomaly["severity"] == "CRITICAL"
    assert anomaly["details"]["zone_id"] == "DERMDOC"
    assert anomaly["details"]["max_dwell_ms"] == 660_000


def test_queue_congestion_detected_from_queue_events(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = [
        event(visitor_id=f"VIS_Q_{idx}", event_type="ENTRY", timestamp=base)
        for idx in range(5)
    ]
    for idx in range(5):
        events.append(
            event(
                visitor_id=f"VIS_Q_{idx}",
                event_type="QUEUE_JOIN",
                timestamp=base + timedelta(seconds=idx),
                zone_id="CASH_COUNTER",
            )
        )
    events.append(
        event(
            visitor_id="VIS_Q_0",
            event_type="QUEUE_EXIT",
            timestamp=base + timedelta(minutes=4),
            zone_id="CASH_COUNTER",
            dwell_ms=240_000,
        )
    )
    ingest(client, events)

    anomaly = _anomalies_by_type(client.get("/stores/STORE_BLR_002/anomalies").json())[
        "QUEUE_CONGESTION"
    ]

    assert anomaly["severity"] == "WARN"
    assert anomaly["details"]["max_depth"] == 5
    assert anomaly["details"]["peak_wait_time"] == 240_000


def test_traffic_spike_detected_against_previous_window(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = [
        event(visitor_id="VIS_PREV", event_type="ENTRY", timestamp=base - timedelta(minutes=20))
    ]
    for idx in range(6):
        events.append(
            event(
                visitor_id=f"VIS_SPIKE_{idx}",
                event_type="ENTRY",
                timestamp=base - timedelta(minutes=idx),
            )
        )
    ingest(client, events)

    anomaly = _anomalies_by_type(client.get("/stores/STORE_BLR_002/anomalies").json())[
        "TRAFFIC_SPIKE"
    ]

    assert anomaly["severity"] == "CRITICAL"
    assert anomaly["details"]["current_entries"] == 6
    assert anomaly["details"]["previous_entries"] == 1


def test_reentry_spike_detected(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = []
    for idx in range(4):
        events.append(
            event(
                visitor_id=f"VIS_RE_{idx}",
                event_type="REENTRY",
                timestamp=base + timedelta(minutes=idx),
            )
        )
    for idx in range(4):
        events.append(
            event(
                visitor_id=f"VIS_NEW_{idx}",
                event_type="ENTRY",
                timestamp=base + timedelta(minutes=idx),
            )
        )
    ingest(client, events)

    anomaly = _anomalies_by_type(client.get("/stores/STORE_BLR_002/anomalies").json())[
        "REENTRY_SPIKE"
    ]

    assert anomaly["severity"] == "CRITICAL"
    assert anomaly["details"]["reentry_count"] == 4
    assert anomaly["details"]["reentry_ratio"] == 0.5


def test_low_conversion_period_detected_from_purchase_events(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = []
    for idx in range(6):
        events.append(
            event(
                visitor_id=f"VIS_LOW_{idx}",
                event_type="ENTRY",
                timestamp=base + timedelta(minutes=idx),
            )
        )
    events.append(
        event(
            visitor_id="VIS_LOW_0",
            event_type="PURCHASE",
            timestamp=base + timedelta(minutes=10),
            metadata={"transaction_id": "TXN_LOW_0"},
        )
    )
    ingest(client, events)

    anomaly = _anomalies_by_type(client.get("/stores/STORE_BLR_002/anomalies").json())[
        "LOW_CONVERSION"
    ]

    assert anomaly["severity"] == "WARN"
    assert anomaly["details"]["visitors"] == 6
    assert anomaly["details"]["purchases"] == 1
    assert anomaly["details"]["conversion_rate"] == 0.1667


def test_pos_mismatch_anomaly_detects_unmatched_transactions(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    ingest(client, [event(visitor_id="VIS_ONLY", event_type="ENTRY", timestamp=base)])
    _insert_pos("TXN_UNMATCHED_1", base + timedelta(minutes=30))
    _insert_pos("TXN_MATCHED_1", base + timedelta(minutes=1), matched_visitor_id="VIS_ONLY")

    anomaly = _anomalies_by_type(client.get("/stores/STORE_BLR_002/anomalies").json())[
        "POS_MISMATCH"
    ]

    assert anomaly["severity"] == "WARN"
    assert anomaly["details"]["unmatched_transactions"] == 1
    assert anomaly["details"]["sample_transaction_ids"] == ["TXN_UNMATCHED_1"]
