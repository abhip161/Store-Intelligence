# PROMPT:
# Generate tests for event ingest validation, idempotency, deduplication, and
# partial success for the Purplle Store Intelligence API.
# CHANGES MADE:
# Focused assertions on challenge scoring behavior: duplicate event IDs must not
# create duplicate facts, malformed records must be rejected without a 5xx, and
# valid records in the same batch must still be accepted.

from __future__ import annotations

from conftest import event, ingest


def test_ingest_accepts_valid_event(client, now):
    payload = event(timestamp=now)

    result = ingest(client, [payload])

    assert result["accepted"] == 1
    assert result["duplicates"] == 0
    assert result["rejected"] == 0
    assert result["errors"] == []


def test_ingest_is_idempotent_by_event_id(client, now):
    event_id = "11111111-1111-4111-8111-111111111111"
    payload = event(timestamp=now, event_id=event_id)

    first = ingest(client, [payload])
    second = ingest(client, [payload])

    assert first["accepted"] == 1
    assert second["accepted"] == 0
    assert second["duplicates"] == 1
    metrics = client.get("/stores/STORE_BLR_002/metrics").json()
    assert metrics["unique_visitors"] == 1


def test_ingest_partial_success_for_malformed_event(client, now):
    valid = event(timestamp=now)
    invalid = event(timestamp=now)
    invalid["confidence"] = 1.5

    result = ingest(client, [valid, invalid])

    assert result["accepted"] == 1
    assert result["rejected"] == 1
    assert result["errors"][0]["index"] == 1
    assert result["errors"][0]["code"] == "VALIDATION_ERROR"


def test_entry_exit_zone_validation_rejects_zone_id(client, now):
    invalid = event(timestamp=now, event_type="ENTRY", zone_id="DOOR")

    result = ingest(client, [invalid])

    assert result["accepted"] == 0
    assert result["rejected"] == 1
