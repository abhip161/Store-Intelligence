from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.config import get_settings
from app.repository import iso

from conftest import event, ingest


def insert_pos_transaction(
    *,
    transaction_id: str,
    store_id: str,
    timestamp,
    basket_value: float,
) -> None:
    connection = sqlite3.connect(get_settings().database_path)
    connection.execute(
        """
        INSERT INTO pos_transactions (
            transaction_id, store_id, timestamp, basket_value_inr, basket_value,
            product, brand, salesperson, matched_visitor_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            transaction_id,
            store_id,
            iso(timestamp),
            basket_value,
            basket_value,
            "Lipstick",
            "Lakme",
            "Store Associate",
        ),
    )
    connection.commit()
    connection.close()


def test_metrics_attribute_pos_transaction_to_nearest_visitor_session(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = [
        event(visitor_id="VIS_EARLY", event_type="ENTRY", timestamp=base),
        event(
            visitor_id="VIS_EARLY",
            event_type="BILLING_QUEUE_JOIN",
            timestamp=base + timedelta(minutes=3),
            zone_id="BILLING",
            metadata={"queue_depth": 1, "sku_zone": "BILLING"},
        ),
        event(visitor_id="VIS_LATE", event_type="ENTRY", timestamp=base + timedelta(minutes=10)),
        event(
            visitor_id="VIS_LATE",
            event_type="BILLING_QUEUE_JOIN",
            timestamp=base + timedelta(minutes=13),
            zone_id="BILLING",
            metadata={"queue_depth": 1, "sku_zone": "BILLING"},
        ),
    ]
    ingest(client, events)
    insert_pos_transaction(
        transaction_id="TXN_NEAREST",
        store_id="STORE_BLR_002",
        timestamp=base + timedelta(minutes=12),
        basket_value=999.5,
    )

    first = client.get("/stores/STORE_BLR_002/metrics")
    second = client.get("/stores/STORE_BLR_002/metrics")

    assert first.status_code == 200
    assert first.json()["conversion_rate"] == 0.5
    assert second.json()["conversion_rate"] == 0.5

    connection = sqlite3.connect(get_settings().database_path)
    connection.row_factory = sqlite3.Row
    attribution_count = connection.execute(
        "SELECT COUNT(*) FROM purchase_attributions WHERE transaction_id = ?",
        ("TXN_NEAREST",),
    ).fetchone()[0]
    purchase_event_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM events
        WHERE event_type = 'PURCHASE' AND json_extract(raw_metadata_json, '$.transaction_id') = ?
        """,
        ("TXN_NEAREST",),
    ).fetchone()[0]
    matched = connection.execute(
        """
        SELECT matched_visitor_id
        FROM pos_transactions
        WHERE transaction_id = ?
        """,
        ("TXN_NEAREST",),
    ).fetchone()
    connection.close()

    assert attribution_count == 0
    assert purchase_event_count == 0
    assert matched["matched_visitor_id"] is None


def test_purchase_attribution_respects_configured_time_window(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    ingest(
        client,
        [
            event(visitor_id="VIS_BROWSER", event_type="ENTRY", timestamp=base),
            event(
                visitor_id="VIS_BROWSER",
                event_type="BILLING_QUEUE_JOIN",
                timestamp=base + timedelta(minutes=1),
                zone_id="BILLING",
                metadata={"queue_depth": 1, "sku_zone": "BILLING"},
            ),
        ],
    )
    insert_pos_transaction(
        transaction_id="TXN_TOO_LATE",
        store_id="STORE_BLR_002",
        timestamp=base + timedelta(minutes=20),
        basket_value=125.0,
    )

    metrics = client.get("/stores/STORE_BLR_002/metrics").json()

    connection = sqlite3.connect(get_settings().database_path)
    attribution_count = connection.execute(
        "SELECT COUNT(*) FROM purchase_attributions WHERE transaction_id = ?",
        ("TXN_TOO_LATE",),
    ).fetchone()[0]
    connection.close()

    assert metrics["conversion_rate"] == 0.0
    assert attribution_count == 0
