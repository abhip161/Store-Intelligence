from __future__ import annotations

from datetime import timedelta

from conftest import event, ingest


def test_heatmap_computes_average_dwell_per_visit(client, now):
    base = now.replace(hour=12, minute=0, second=0)
    ingest(
        client,
        [
            event(visitor_id="VIS_REPEAT", event_type="ENTRY", timestamp=base),
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
                visitor_id="VIS_REPEAT",
                event_type="ZONE_ENTER",
                timestamp=base + timedelta(seconds=30),
                zone_id="SKINCARE",
            ),
            event(
                visitor_id="VIS_REPEAT",
                event_type="ZONE_EXIT",
                timestamp=base + timedelta(seconds=50),
                zone_id="SKINCARE",
                dwell_ms=20_000,
            ),
        ],
    )

    body = client.get("/stores/STORE_BLR_002/heatmap").json()

    skincare = next(zone for zone in body["zones"] if zone["zone_id"] == "SKINCARE")
    assert skincare["visit_count"] == 2
    assert skincare["avg_dwell_ms"] == 15_000.0
    assert skincare["heat_score"] == 100
    assert skincare["data_confidence"] == "LOW"


def test_heatmap_empty_store_returns_empty_zones(client):
    response = client.get("/stores/UNKNOWN_STORE/heatmap")

    assert response.status_code == 200
    assert response.json() == {"store_id": "UNKNOWN_STORE", "zones": []}
