from __future__ import annotations

import json
from pathlib import Path

import httpx
from scripts.replay_events import iter_jsonl_events, replay_events


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self):
        self.posts = []

    def post(self, url, *, json):
        self.posts.append((url, json))
        events = json["events"]
        return FakeResponse(
            {
                "accepted": len(events) - 1 if len(events) > 1 else len(events),
                "rejected": 1 if len(events) > 1 else 0,
                "duplicates": 0,
            }
        )


def write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, separators=(",", ":")) for event in events) + "\n",
        encoding="utf-8",
    )


def test_replay_events_batches_pipeline_jsonl_into_ingest_payload(tmp_path):
    jsonl_path = tmp_path / "events.jsonl"
    events = [
        {
            "event_id": f"11111111-1111-4111-8111-11111111111{idx}",
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_1",
            "visitor_id": f"VIS_{idx}",
            "event_type": "ZONE_ENTER",
            "timestamp": "2026-04-10T12:00:00Z",
            "zone_id": "ENTRY",
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.9,
            "metadata": {"queue_depth": None, "sku_zone": "ENTRY", "session_seq": idx},
        }
        for idx in range(3)
    ]
    write_jsonl(jsonl_path, events)
    client = FakeClient()

    report = replay_events(
        file_path=jsonl_path,
        api_url="http://testserver",
        batch_size=2,
        client=client,
    )

    assert len(client.posts) == 2
    assert client.posts[0][0] == "http://testserver/events/ingest"
    assert client.posts[0][1] == {"events": events[:2]}
    assert client.posts[1][1] == {"events": events[2:]}
    assert report.total_events == 3
    assert report.batches == 2
    assert report.accepted == 2
    assert report.rejected == 1
    assert report.duplicates == 0


def test_iter_jsonl_events_rejects_invalid_lines(tmp_path):
    jsonl_path = tmp_path / "bad.jsonl"
    jsonl_path.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")

    try:
        list(iter_jsonl_events(jsonl_path))
    except ValueError as exc:
        assert "Invalid JSON on line 2" in str(exc)
    else:
        raise AssertionError("invalid JSONL should fail")


def test_replay_events_surfaces_http_errors(tmp_path):
    class FailingClient:
        def post(self, url, *, json):
            request = httpx.Request("POST", url)
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    jsonl_path = tmp_path / "events.jsonl"
    write_jsonl(jsonl_path, [{"event_id": "x"}])

    try:
        replay_events(file_path=jsonl_path, client=FailingClient())
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("HTTP errors should propagate from replay_events")
