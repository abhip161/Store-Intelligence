from __future__ import annotations

import json

from scripts.benchmark_detection import evaluate_benchmark


def test_detection_benchmark_scores_expected_counts(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps({"camera_id": "CAM_1", "event_type": "ENTRY"}),
                json.dumps({"camera_id": "CAM_1", "event_type": "EXIT"}),
                json.dumps({"camera_id": "CAM_1", "event_type": "REENTRY"}),
            ]
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "benchmark.json"
    config_path.write_text(
        json.dumps(
            {
                "events_path": str(events_path),
                "tolerance": 0,
                "cameras": {"CAM_1": {"ENTRY": 1, "EXIT": 1, "REENTRY": 1}},
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_benchmark(config_path)

    assert result["passed"] is True
    assert result["entry_exit_reentry_accuracy"] == 1.0
    assert all(row["passed"] for row in result["rows"])
