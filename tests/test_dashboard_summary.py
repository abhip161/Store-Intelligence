from __future__ import annotations

from datetime import timedelta

from conftest import event, ingest


def test_dashboard_summary_uses_real_event_trends(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    ingest(
        client,
        [
            event(visitor_id="VIS_1", event_type="ENTRY", timestamp=base),
            event(visitor_id="VIS_2", event_type="ENTRY", timestamp=base + timedelta(minutes=5)),
            event(
                visitor_id="VIS_1",
                event_type="PURCHASE",
                timestamp=base + timedelta(minutes=10),
                metadata={"transaction_id": "TXN_1"},
            ),
            event(visitor_id="STAFF_1", event_type="ENTRY", timestamp=base, is_staff=True),
        ],
    )

    response = client.get("/stores/STORE_BLR_002/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["store_id"] == "STORE_BLR_002"
    assert payload["visitor_trend"][0]["visitors"] == 2
    assert payload["conversion_trend"][0]["purchases"] == 1
    assert payload["conversion_trend"][0]["conversion_rate"] == 0.5


def test_dashboard_summary_empty_store_is_empty(client):
    payload = client.get("/stores/UNKNOWN/dashboard").json()

    assert payload["visitor_trend"] == []
    assert payload["conversion_trend"] == []
