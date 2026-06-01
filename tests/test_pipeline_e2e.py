from __future__ import annotations

import json
from datetime import UTC, datetime

import cv2
import numpy as np
from pipeline.detector import YoloPersonDetector
from pipeline.pipeline_runner import DetectionPipelineRunner
from pipeline.schemas import CameraConfig, PipelineConfig, Point


class _Value:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class _Vector:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class _Box:
    def __init__(self, *, track_id: int, xyxy: list[float], confidence: float) -> None:
        self.id = [_Value(track_id)]
        self.cls = [_Value(YoloPersonDetector.PERSON_CLASS_ID)]
        self.conf = [_Value(confidence)]
        self.xyxy = [_Vector(xyxy)]


class _Boxes:
    def __init__(self, boxes: list[_Box]) -> None:
        self._boxes = boxes
        self.id = object()

    def __iter__(self):
        return iter(self._boxes)


class _Result:
    def __init__(self, boxes: list[_Box]) -> None:
        self.boxes = _Boxes(boxes)


class _FakeYoloModel:
    def track(self, **_kwargs):
        yield _Result([_Box(track_id=7, xyxy=[20, 10, 40, 40], confidence=0.91)])
        yield _Result([_Box(track_id=7, xyxy=[20, 40, 40, 70], confidence=0.89)])


def _write_tiny_video(path) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        5.0,
        (96, 96),
    )
    assert writer.isOpened()
    try:
        for _ in range(2):
            writer.write(np.zeros((96, 96, 3), dtype=np.uint8))
    finally:
        writer.release()


def test_pipeline_runner_generates_structured_entry_event(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.mp4"
    output_path = tmp_path / "events.jsonl"
    _write_tiny_video(video_path)
    monkeypatch.setattr(YoloPersonDetector, "_load_model", lambda _self: _FakeYoloModel())

    runner = DetectionPipelineRunner(
        PipelineConfig(
            zone_config_path=None,
            max_frames=2,
        )
    )
    event_count = runner.run(
        [
            CameraConfig(
                store_id="STORE_TEST",
                camera_id="CAM_TEST",
                video_path=str(video_path),
                entry_line=(Point(0, 50), Point(96, 50)),
                inbound_side=1,
                start_time=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            )
        ],
        output_path,
    )

    lines = output_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    assert event_count == 1
    assert payload["store_id"] == "STORE_TEST"
    assert payload["camera_id"] == "CAM_TEST"
    assert payload["event_type"] == "ENTRY"
    assert payload["timestamp"] == "2026-06-01T10:00:00Z"
    assert payload["confidence"] == 0.89
    assert payload["metadata"]["track_id"] == 7
    assert payload["metadata"]["crossing"] == "INBOUND"
