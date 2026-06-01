# PROMPT:
# Generate tests for a real conversion funnel computed from actual persisted
# event data only: ENTRY -> ZONE_VISIT -> CASH_COUNTER -> PURCHASE.
# CHANGES MADE:
# Replaced the old simplified billing funnel tests with sequential stage,
# dropoff, conversion, staff-exclusion, and duplicate-event assertions.

from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.config import get_settings
from app.repository import iso

from conftest import event, ingest


def _stage_map(response_json):
    return {stage["stage"]: stage for stage in response_json["stages"]}


def _customer_events(visitor_id: str, base, *, purchase: bool = False):
    events = [
        event(visitor_id=visitor_id, event_type="ENTRY", timestamp=base),
        event(
            visitor_id=visitor_id,
            camera_id="CAM_FLOOR_01",
            event_type="ZONE_ENTER",
            timestamp=base + timedelta(minutes=1),
            zone_id="SKINCARE",
            metadata={"sku_zone": "SKINCARE", "session_seq": 2},
        ),
        event(
            visitor_id=visitor_id,
            camera_id="CAM_CASH_01",
            event_type="ZONE_ENTER",
            timestamp=base + timedelta(minutes=2),
            zone_id="CASH_COUNTER",
            metadata={"sku_zone": "CASH_COUNTER", "session_seq": 3},
        ),
    ]
    if purchase:
        events.append(
            event(
                visitor_id=visitor_id,
                camera_id="POS",
                event_type="PURCHASE",
                timestamp=base + timedelta(minutes=3),
                confidence=0.95,
                metadata={
                    "transaction_id": f"TXN_{visitor_id}",
                    "basket_value": 499.0,
                    "attribution_confidence": 0.95,
                    "source": "pos_attribution",
                },
            )
        )
    return events


def test_funnel_uses_real_event_data_and_sequential_cash_counter_stage(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = []
    events.extend(_customer_events("VIS_BUYER", base, purchase=True))
    events.extend(_customer_events("VIS_CASH_ONLY", base + timedelta(minutes=10)))
    events.extend(
        [
            event(
                visitor_id="VIS_ZONE_ONLY",
                event_type="ENTRY",
                timestamp=base + timedelta(minutes=20),
            ),
            event(
                visitor_id="VIS_ZONE_ONLY",
                event_type="ZONE_ENTER",
                timestamp=base + timedelta(minutes=21),
                zone_id="SKINCARE",
            ),
            event(
                visitor_id="VIS_ENTRY_ONLY",
                event_type="ENTRY",
                timestamp=base + timedelta(minutes=30),
            ),
        ]
    )
    ingest(client, events)

    response = client.get("/stores/STORE_BLR_002/funnel")

    assert response.status_code == 200
    funnel = response.json()
    assert funnel["unit"] == "session"
    stages = _stage_map(funnel)
    assert list(stages) == ["ENTRY", "ZONE_VISIT", "CASH_COUNTER", "PURCHASE"]
    assert stages["ENTRY"] == {
        "stage": "ENTRY",
        "count": 4,
        "dropoff_pct": 0.0,
        "conversion_pct": 100.0,
    }
    assert stages["ZONE_VISIT"] == {
        "stage": "ZONE_VISIT",
        "count": 3,
        "dropoff_pct": 25.0,
        "conversion_pct": 75.0,
    }
    assert stages["CASH_COUNTER"] == {
        "stage": "CASH_COUNTER",
        "count": 2,
        "dropoff_pct": 33.33,
        "conversion_pct": 50.0,
    }
    assert stages["PURCHASE"] == {
        "stage": "PURCHASE",
        "count": 1,
        "dropoff_pct": 50.0,
        "conversion_pct": 25.0,
    }


def test_funnel_requires_sequential_stage_progression(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    events = [
        event(visitor_id="VIS_CASH_WITHOUT_ZONE", event_type="ENTRY", timestamp=base),
        event(
            visitor_id="VIS_CASH_WITHOUT_ZONE",
            event_type="ZONE_ENTER",
            timestamp=base + timedelta(minutes=1),
            zone_id="CASH_COUNTER",
        ),
        event(
            visitor_id="VIS_PURCHASE_WITHOUT_CASH",
            event_type="ENTRY",
            timestamp=base + timedelta(minutes=10),
        ),
        event(
            visitor_id="VIS_PURCHASE_WITHOUT_CASH",
            event_type="ZONE_ENTER",
            timestamp=base + timedelta(minutes=11),
            zone_id="SKINCARE",
        ),
        event(
            visitor_id="VIS_PURCHASE_WITHOUT_CASH",
            event_type="PURCHASE",
            timestamp=base + timedelta(minutes=12),
            metadata={"transaction_id": "TXN_NO_CASH"},
        ),
    ]
    ingest(client, events)

    stages = _stage_map(client.get("/stores/STORE_BLR_002/funnel").json())

    assert stages["ENTRY"]["count"] == 2
    assert stages["ZONE_VISIT"]["count"] == 2
    assert stages["CASH_COUNTER"]["count"] == 1
    assert stages["PURCHASE"]["count"] == 0


def test_funnel_excludes_staff_and_duplicate_events(client, now):
    event_id = "33333333-3333-4333-8333-333333333333"
    base = now.replace(hour=12, minute=0, second=0)
    customer_entry = event(visitor_id="VIS_CUSTOMER", event_type="ENTRY", timestamp=base)
    staff_events = _customer_events("STAFF_001", base + timedelta(minutes=5), purchase=True)
    staff_events = [{**payload, "is_staff": True} for payload in staff_events]

    ingest(client, [customer_entry, event(visitor_id="VIS_DUP", timestamp=base, event_id=event_id)])
    ingest(client, [event(visitor_id="VIS_DUP", timestamp=base, event_id=event_id)])
    ingest(client, staff_events)

    stages = _stage_map(client.get("/stores/STORE_BLR_002/funnel").json())

    assert stages["ENTRY"]["count"] == 2
    assert stages["ZONE_VISIT"]["count"] == 0
    assert stages["CASH_COUNTER"]["count"] == 0
    assert stages["PURCHASE"]["count"] == 0


def test_funnel_attributes_pos_before_counting_purchase_stage(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    ingest(
        client,
        [
            event(visitor_id="VIS_POS", event_type="ENTRY", timestamp=base),
            event(
                visitor_id="VIS_POS",
                event_type="ZONE_ENTER",
                timestamp=base + timedelta(minutes=1),
                zone_id="SKINCARE",
            ),
            event(
                visitor_id="VIS_POS",
                event_type="ZONE_ENTER",
                timestamp=base + timedelta(minutes=2),
                zone_id="CASH_COUNTER",
                metadata={"sku_zone": "CASH_COUNTER"},
            ),
        ],
    )
    connection = sqlite3.connect(get_settings().database_path)
    connection.execute(
        """
        INSERT INTO pos_transactions (
            transaction_id, store_id, timestamp, basket_value_inr, basket_value,
            product, brand, salesperson, matched_visitor_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            "TXN_FUNNEL",
            "STORE_BLR_002",
            iso(base + timedelta(minutes=3)),
            499.0,
            499.0,
            "Lipstick",
            "Lakme",
            "Associate",
        ),
    )
    connection.commit()
    connection.close()

    stages = _stage_map(client.get("/stores/STORE_BLR_002/funnel").json())

    assert stages["PURCHASE"]["count"] == 1
