from __future__ import annotations

from datetime import timedelta

from conftest import event, ingest, iso


def test_analytics_accept_explicit_historical_window(client, now):
    base = (now - timedelta(days=2)).replace(hour=12, minute=0, second=0)
    ingest(
        client,
        [
            event(visitor_id="VIS_HIST", event_type="ENTRY", timestamp=base),
            event(
                visitor_id="VIS_HIST",
                event_type="ZONE_ENTER",
                timestamp=base + timedelta(minutes=1),
                zone_id="SKINCARE",
            ),
            event(
                visitor_id="VIS_HIST",
                event_type="ZONE_EXIT",
                timestamp=base + timedelta(minutes=3),
                zone_id="SKINCARE",
                dwell_ms=120_000,
            ),
            event(
                visitor_id="VIS_HIST",
                event_type="ZONE_ENTER",
                timestamp=base + timedelta(minutes=4),
                zone_id="CASH_COUNTER",
                metadata={"sku_zone": "CASH_COUNTER"},
            ),
            event(
                visitor_id="VIS_HIST",
                event_type="PURCHASE",
                timestamp=base + timedelta(minutes=5),
                metadata={"transaction_id": "TXN_HIST"},
            ),
        ],
    )
    params = {
        "start": iso(base - timedelta(minutes=1)),
        "end": iso(base + timedelta(minutes=10)),
    }

    default_metrics = client.get("/stores/STORE_BLR_002/metrics").json()
    metrics = client.get("/stores/STORE_BLR_002/metrics", params=params).json()
    funnel = client.get("/stores/STORE_BLR_002/funnel", params=params).json()
    heatmap = client.get("/stores/STORE_BLR_002/heatmap", params=params).json()

    assert default_metrics["unique_visitors"] == 0
    assert metrics["unique_visitors"] == 1
    assert metrics["conversion_rate"] == 1.0
    assert metrics["window"]["start"] == params["start"]
    assert funnel["stages"][-1]["count"] == 1
    assert heatmap["zones"][0]["zone_id"] == "CASH_COUNTER"
    assert {zone["zone_id"] for zone in heatmap["zones"]} == {"CASH_COUNTER", "SKINCARE"}
