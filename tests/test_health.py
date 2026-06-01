# PROMPT:
# Generate health endpoint tests for empty systems, healthy feeds, and stale
# feeds using real ingested events.
# CHANGES MADE:
# Kept assertions tied to the challenge acceptance gate: /health must be accurate
# and useful to an on-call engineer.

from __future__ import annotations

from datetime import timedelta

from conftest import event, ingest


def test_health_empty_system_is_ok_with_no_stores(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["database"] == "OK"
    assert body["stores"] == []


def test_health_reports_store_last_event(client, now):
    ingest(client, [event(timestamp=now)])

    body = client.get("/health").json()

    store = body["stores"][0]
    assert store["store_id"] == "STORE_BLR_002"
    assert store["last_event_timestamp"].endswith("Z")
    assert store["feed_status"] == "OK"
    assert store["lag_seconds"] >= 0


def test_health_reports_stale_feed(client, now):
    ingest(client, [event(timestamp=now - timedelta(minutes=30))])

    body = client.get("/health").json()

    assert body["status"] == "DEGRADED"
    assert body["stores"][0]["feed_status"] == "STALE_FEED"
