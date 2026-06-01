# PROMPT:
# Build pytest fixtures for the Store Intelligence Challenge API. Tests must use
# real FastAPI requests, an isolated SQLite database, and computed event payloads.
# CHANGES MADE:
# Added deterministic helpers for UTC timestamps, UUID event generation, and a
# per-test TestClient with an isolated database path.

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("STORE_INTEL_DATABASE_PATH", str(tmp_path / "test_store_intel.db"))
    monkeypatch.setenv("STORE_INTEL_STALE_FEED_MINUTES", "10")
    monkeypatch.setenv("STORE_INTEL_POS_CONVERSION_WINDOW_MINUTES", "5")

    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def event(
    *,
    store_id: str = "STORE_BLR_002",
    camera_id: str = "CAM_ENTRY_01",
    visitor_id: str = "VIS_001",
    event_type: str = "ENTRY",
    timestamp: datetime,
    zone_id: str | None = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.91,
    metadata: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": iso(timestamp),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {
            "queue_depth": None,
            "sku_zone": None,
            "session_seq": 1,
            **(metadata or {}),
        },
    }


def ingest(client: TestClient, events: list[dict[str, Any]]) -> dict[str, Any]:
    response = client.post("/events/ingest", json={"events": events})
    assert response.status_code == 200, response.text
    return response.json()


def visitor_session_events(
    *,
    visitor_id: str,
    start: datetime,
    store_id: str = "STORE_BLR_002",
    purchase: bool = False,
    reentry: bool = False,
    is_staff: bool = False,
    queue_depth: int = 2,
) -> list[dict[str, Any]]:
    events = [
        event(
            store_id=store_id,
            visitor_id=visitor_id,
            event_type="REENTRY" if reentry else "ENTRY",
            timestamp=start,
            is_staff=is_staff,
        ),
        event(
            store_id=store_id,
            visitor_id=visitor_id,
            camera_id="CAM_FLOOR_01",
            event_type="ZONE_ENTER",
            timestamp=start + timedelta(minutes=1),
            zone_id="SKINCARE",
            is_staff=is_staff,
            metadata={"sku_zone": "MOISTURISER", "session_seq": 2},
        ),
        event(
            store_id=store_id,
            visitor_id=visitor_id,
            camera_id="CAM_FLOOR_01",
            event_type="ZONE_DWELL",
            timestamp=start + timedelta(minutes=2),
            zone_id="SKINCARE",
            dwell_ms=60_000,
            is_staff=is_staff,
            metadata={"sku_zone": "MOISTURISER", "session_seq": 3},
        ),
        event(
            store_id=store_id,
            visitor_id=visitor_id,
            camera_id="CAM_BILLING_01",
            event_type="BILLING_QUEUE_JOIN",
            timestamp=start + timedelta(minutes=3),
            zone_id="BILLING",
            is_staff=is_staff,
            metadata={"queue_depth": queue_depth, "sku_zone": "BILLING", "session_seq": 4},
        ),
    ]
    if purchase:
        events.append(
            event(
                store_id=store_id,
                visitor_id=visitor_id,
                camera_id="CAM_BILLING_01",
                event_type="ZONE_DWELL",
                timestamp=start + timedelta(minutes=4),
                zone_id="BILLING",
                dwell_ms=30_000,
                is_staff=is_staff,
                metadata={
                    "sku_zone": "BILLING",
                    "session_seq": 5,
                    "purchase": True,
                    "transaction_id": f"TXN_{visitor_id}",
                },
            )
        )
    return events
