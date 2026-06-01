from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2

from pipeline.schemas import BoundingBox, Detection

LOGGER = logging.getLogger(__name__)


class YoloPersonDetector:
    """YOLOv8 person detector with lazy model loading.

    The model is loaded only when detection starts so API-only workflows do not
    pay the import/model cost. Low-confidence person detections above the
    configured minimum are returned with their raw confidence for downstream
    calibration.
    """

    PERSON_CLASS_ID = 0

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence_threshold: float = 0.25,
        iou_threshold: float = 0.5,
    ) -> None:
        self.model_name = model_name
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from ultralytics import YOLO
            except ImportError as exc:
                raise RuntimeError(
                    "ultralytics is required for YOLOv8 detection. Install requirements.txt."
                ) from exc

            LOGGER.info("loading_yolo_model", extra={"model_name": self.model_name})
            self._model = YOLO(self.model_name)
        return self._model

    def iter_video_detections(
        self,
        video_path: str | Path,
        *,
        frame_stride: int = 1,
        max_frames: int | None = None,
        start_time: datetime | None = None,
    ) -> Iterator[list[Detection]]:
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video file does not exist: {video_path}")
        if frame_stride < 1:
            raise ValueError("frame_stride must be >= 1")

        model = self._load_model()
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise RuntimeError(f"Unable to open video file: {video_path}")

        fps = capture.get(cv2.CAP_PROP_FPS) or 15.0
        base_time = start_time or datetime.now(UTC)
        frame_index = -1

        LOGGER.info(
            "video_detection_started",
            extra={"video_path": str(video_path), "fps": fps, "frame_stride": frame_stride},
        )

        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frame_index += 1
                if max_frames is not None and frame_index >= max_frames:
                    break
                if frame_index % frame_stride != 0:
                    continue

                timestamp = base_time + timedelta(seconds=frame_index / fps)
                try:
                    results = model.predict(
                        frame,
                        classes=[self.PERSON_CLASS_ID],
                        conf=self.confidence_threshold,
                        device="cpu",
                        iou=self.iou_threshold,
                        verbose=False,
                    )
                except Exception:
                    LOGGER.exception(
                        "yolo_prediction_failed",
                        extra={"video_path": str(video_path), "frame_index": frame_index},
                    )
                    yield []
                    continue

                detections: list[Detection] = []
                for result in results:
                    boxes = getattr(result, "boxes", None)
                    if boxes is None:
                        continue
                    for box in boxes:
                        class_id = int(box.cls[0].item()) if box.cls is not None else -1
                        if class_id != self.PERSON_CLASS_ID:
                            continue
                        confidence = float(box.conf[0].item()) if box.conf is not None else 0.0
                        x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
                        detections.append(
                            Detection(
                                bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                                confidence=confidence,
                                class_id=class_id,
                                class_name="person",
                                frame_index=frame_index,
                                timestamp=timestamp,
                            )
                        )
                yield detections
        finally:
            capture.release()
            LOGGER.info("video_detection_finished", extra={"video_path": str(video_path)})
