from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_BATCH_SIZE = 250


@dataclass
class ReplayReport:
    total_events: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    batches: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def iter_jsonl_events(path: str | Path):
    jsonl_path = Path(path)
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Events JSONL file does not exist: {jsonl_path}")

    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc.msg}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"Line {line_number} must contain a JSON object")
            yield event


def batched(items, batch_size: int):
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def post_batch(client: httpx.Client, api_url: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    endpoint = f"{api_url.rstrip('/')}/events/ingest"
    response = client.post(endpoint, json={"events": events})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Ingestion API returned a non-object JSON payload")
    return payload


def replay_events(
    *,
    file_path: str | Path,
    api_url: str = DEFAULT_API_URL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    client: httpx.Client | None = None,
) -> ReplayReport:
    report = ReplayReport()
    owns_client = client is None
    active_client = client or httpx.Client(timeout=30.0)
    try:
        for batch in batched(iter_jsonl_events(file_path), batch_size):
            report.batches += 1
            report.total_events += len(batch)
            payload = post_batch(active_client, api_url, batch)
            report.accepted += int(payload.get("accepted", 0))
            report.rejected += int(payload.get("rejected", 0))
            report.duplicates += int(payload.get("duplicates", 0))
    finally:
        if owns_client:
            active_client.close()
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay pipeline JSONL events into the ingest API."
    )
    parser.add_argument("--file", required=True, help="Path to pipeline-generated JSONL events.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    try:
        report = replay_events(
            file_path=args.file,
            api_url=args.api_url,
            batch_size=args.batch_size,
        )
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"Replay failed: {exc}", file=sys.stderr)
        return 1

    print(f"accepted={report.accepted}")
    print(f"rejected={report.rejected}")
    print(f"duplicates={report.duplicates}")
    return 1 if report.rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
