from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

COUNTED_EVENT_TYPES = {"ENTRY", "EXIT", "REENTRY"}


def load_jsonl_events(path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} is not valid JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            events.append(payload)
    return events


def count_events(events: list[dict[str, Any]]) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for event in events:
        camera_id = str(event.get("camera_id") or "")
        event_type = str(event.get("event_type") or "")
        if camera_id and event_type in COUNTED_EVENT_TYPES:
            counts[(camera_id, event_type)] += 1
    return counts


def evaluate_benchmark(config_path: str | Path) -> dict[str, Any]:
    config_path = Path(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    events_path = Path(config["events_path"])
    if not events_path.is_absolute():
        events_path = config_path.parent.parent / events_path

    tolerance = int(config.get("tolerance", 0))
    counts = count_events(load_jsonl_events(events_path))
    rows: list[dict[str, Any]] = []

    for camera_id, expected_counts in sorted(config.get("cameras", {}).items()):
        for event_type, expected in sorted(expected_counts.items()):
            actual = counts[(camera_id, event_type)]
            delta = actual - int(expected)
            rows.append(
                {
                    "camera_id": camera_id,
                    "event_type": event_type,
                    "expected": int(expected),
                    "actual": actual,
                    "delta": delta,
                    "passed": abs(delta) <= tolerance,
                }
            )

    passed = all(row["passed"] for row in rows)
    total_expected = sum(row["expected"] for row in rows)
    total_absolute_error = sum(abs(row["delta"]) for row in rows)
    accuracy = 1.0 if total_expected == 0 else 1.0 - (total_absolute_error / total_expected)

    return {
        "name": config.get("name", config_path.stem),
        "events_path": str(events_path),
        "tolerance": tolerance,
        "passed": passed,
        "entry_exit_reentry_accuracy": round(max(0.0, accuracy), 4),
        "rows": rows,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark generated detection events.")
    parser.add_argument("--config", default="data/detection_benchmark.json")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    result = evaluate_benchmark(args.config)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Benchmark: {result['name']}")
        print(f"Events: {result['events_path']}")
        print(f"Accuracy: {result['entry_exit_reentry_accuracy']:.2%}")
        for row in result["rows"]:
            status = "PASS" if row["passed"] else "FAIL"
            print(
                f"{status} {row['camera_id']} {row['event_type']}: "
                f"expected={row['expected']} actual={row['actual']} delta={row['delta']}"
            )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
