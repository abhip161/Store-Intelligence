from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pipeline.detector import YoloPersonDetector
from pipeline.event_builder import EventBuilder
from pipeline.line_crossing import EntryExitLine
from pipeline.reid import VisitorIdManager
from pipeline.schemas import CameraConfig, PipelineConfig, Point, StoreEvent
from pipeline.tracker import EventTracker, UltralyticsByteTrackAdapter
from pipeline.zones import ZoneEngine, load_zone_config

LOGGER = logging.getLogger(__name__)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class DetectionPipelineRunner:
    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.detector = YoloPersonDetector(
            model_name=config.model_name,
            confidence_threshold=config.confidence_threshold,
            iou_threshold=config.iou_threshold,
        )
        self._zone_configs = {}

    def _zone_engine_for(self, store_id: str) -> ZoneEngine | None:
        if self.config.zone_config_path is None:
            return None
        if store_id not in self._zone_configs:
            self._zone_configs[store_id] = load_zone_config(
                self.config.zone_config_path,
                store_id=store_id,
            )
        return ZoneEngine(
            self._zone_configs[store_id],
            dwell_interval_ms=self.config.dwell_emit_seconds * 1000,
        )

    def run_camera(self, camera: CameraConfig) -> Iterable[StoreEvent]:
        LOGGER.info(
            "camera_pipeline_started",
            extra={
                "store_id": camera.store_id,
                "camera_id": camera.camera_id,
                "video_path": camera.video_path,
            },
        )
        track_adapter = UltralyticsByteTrackAdapter(
            detector=self.detector,
            frame_stride=self.config.frame_stride,
            max_frames=self.config.max_frames,
        )
        visitor_ids = VisitorIdManager(
            store_id=camera.store_id,
            camera_id=camera.camera_id,
            visitor_prefix=self.config.visitor_prefix,
            reentry_window_seconds=self.config.reentry_window_seconds,
        )
        event_tracker = EventTracker(
            store_id=camera.store_id,
            camera_id=camera.camera_id,
            entry_line=EntryExitLine(
                camera.entry_line[0],
                camera.entry_line[1],
                inbound_side=camera.inbound_side,
            ),
            visitor_ids=visitor_ids,
            event_builder=EventBuilder(store_id=camera.store_id, camera_id=camera.camera_id),
            zone_engine=self._zone_engine_for(camera.store_id),
            staff_zone_ids=self.config.staff_zone_ids,
        )

        try:
            for observations in track_adapter.iter_video_observations(
                camera.video_path,
                start_time=camera.start_time or datetime.now(UTC),
            ):
                yield from event_tracker.update(observations)
        except Exception:
            LOGGER.exception(
                "camera_pipeline_failed",
                extra={"store_id": camera.store_id, "camera_id": camera.camera_id},
            )
            raise
        finally:
            LOGGER.info(
                "camera_pipeline_finished",
                extra={"store_id": camera.store_id, "camera_id": camera.camera_id},
            )

    def run(self, cameras: list[CameraConfig], output_path: str | Path) -> int:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0

        with output_path.open("w", encoding="utf-8") as handle:
            for camera in cameras:
                for event in self.run_camera(camera):
                    handle.write(json.dumps(event.to_dict(), separators=(",", ":")) + "\n")
                    count += 1

        LOGGER.info(
            "pipeline_output_written",
            extra={"output_path": str(output_path), "count": count},
        )
        return count


def _parse_point(value: str) -> Point:
    try:
        x_raw, y_raw = value.split(",", maxsplit=1)
        return Point(float(x_raw), float(y_raw))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("point must be formatted as x,y") from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run YOLOv8 + tracking event generation.")
    parser.add_argument("--video", required=True, help="Path to a CCTV clip.")
    parser.add_argument("--store-id", default="STORE_BLR_002")
    parser.add_argument("--camera-id", default="CAM_ENTRY_01")
    parser.add_argument(
        "--line-start",
        type=_parse_point,
        required=True,
        help="Entry line start x,y.",
    )
    parser.add_argument("--line-end", type=_parse_point, required=True, help="Entry line end x,y.")
    parser.add_argument("--inbound-side", type=int, default=1, choices=[-1, 1])
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--frame-stride", type=int, default=1)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--output", default="data/events.jsonl")
    parser.add_argument("--zone-config", default="data/zone_config.json")
    parser.add_argument("--dwell-seconds", type=int, default=30)
    parser.add_argument(
        "--staff-zone",
        action="append",
        default=None,
        help="Zone id that should mark a track as staff. Can be repeated.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    configure_logging(args.log_level)
    config = PipelineConfig(
        model_name=args.model,
        confidence_threshold=args.confidence,
        frame_stride=args.frame_stride,
        max_frames=args.max_frames,
        output_jsonl=args.output,
        zone_config_path=args.zone_config,
        dwell_emit_seconds=args.dwell_seconds,
        staff_zone_ids=tuple(args.staff_zone) if args.staff_zone else ("STAFF_ONLY", "BACK_OFFICE"),
    )
    camera = CameraConfig(
        store_id=args.store_id,
        camera_id=args.camera_id,
        video_path=args.video,
        entry_line=(args.line_start, args.line_end),
        inbound_side=args.inbound_side,
        start_time=datetime.now(UTC),
    )
    runner = DetectionPipelineRunner(config)
    count = runner.run([camera], args.output)
    LOGGER.info("pipeline_complete", extra={"event_count": count})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
