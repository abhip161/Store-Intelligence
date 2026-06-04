# Store Intelligence Challenge

CCTV-derived retail intelligence for a Purplle-style store. The system turns person tracks into auditable store events, stores them in SQLite, and exposes APIs for visitor metrics, funnel, heatmap, anomalies, health, and a Streamlit dashboard.

# Quick Start

```bash
git clone https://github.com/abhip161/Store-Intelligence.git
cd Store-Intelligence
```

```bash
docker compose up --build
```

This starts the API and dashboard only. The heavier video pipeline image is optional and is built only when its Compose profile is enabled.

```bash
docker compose --profile pipeline up --build
```

# Typical Workflow

```text
Clone Repo
    ↓
Start API + Dashboard
    ↓
Enable Pipeline Profile
    ↓
Place CCTV Video
    ↓
Run pipeline_runner
    ↓
Generate events.jsonl
    ↓
Replay Events
    ↓
View Dashboard Analytics
```

# Process Your Own CCTV Videos

Place CCTV videos inside:

```text
data/CCTV Footage/
```

Start the pipeline profile:

```bash
docker compose --profile pipeline up --build
```

Process a video:

```bash
docker compose exec pipeline python -m pipeline.pipeline_runner \
  --video "/app/data/CCTV Footage/entry 2.mp4" \
  --camera-id BILLING \
  --line-start 0,500 \
  --line-end 1920,500 \
  --frame-stride 5 \
  --output /app/data/events.jsonl
```
## Replay Events Into The API

```bash
docker compose exec pipeline python /app/scripts/replay_events.py \
  --file /app/data/events.jsonl \
  --api-url http://api:8000
```

This loads generated events into the API database so analytics become visible in the dashboard.

## View Results

- Dashboard: `http://localhost:8501`
- Swagger Docs: `http://localhost:8000/docs`
- Health Check: `http://localhost:8000/health`

Funnel analytics, heatmaps, visitor metrics, queue metrics, and anomaly detection update after event ingestion.

## Quick Validation

Start the API and dashboard:

```bash
docker compose up --build
```

Verify:

- Health: `http://localhost:8000/health`
- Swagger API Docs: `http://localhost:8000/docs`
- Dashboard: `http://localhost:8501`

Run quality gates:

```bash
ruff check .
pytest
```

Run local tests:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

On macOS/Linux, activate with `source .venv/bin/activate`.

## What This Solves

Retail CCTV is useful only after raw detections become business facts. This project converts video observations into a normalized event stream:

- `ENTRY`, `EXIT`, `REENTRY`
- `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`
- `QUEUE_JOIN`, `QUEUE_EXIT`
- `PURCHASE`

The API computes metrics from persisted events and POS rows, not hardcoded counters.

## Architecture

```text
CCTV clip
  -> YOLOv8 person detection
  -> ByteTrack tracking
  -> line crossing + zone engine
  -> event JSONL
  -> POST /events/ingest
  -> SQLite
  -> metrics / funnel / heatmap / anomalies / health APIs
  -> Streamlit dashboard
```

Main packages:

```text
app/          FastAPI app, validation, analytics, repository, API routers
pipeline/     YOLO detector, tracking adapters, zone engine, event builder
dashboard/    Streamlit dashboard using only public API endpoints
scripts/      POS loader, event replay utility, and detection benchmark
tests/        API, analytics, ingestion, POS, benchmark, and pipeline tests
data/         Sample SQLite DB, POS CSV, zone config, and CCTV clips
```

## Detection Validation

The repository includes a repeatable count benchmark in `data/detection_benchmark.json`, executed by `scripts/benchmark_detection.py` and covered by `tests/test_detection_benchmark.py`.

Run:

```bash
python scripts/benchmark_detection.py --config data/detection_benchmark.json --json
```

Benchmark artifact:

- Name: `sample-cam3-event-benchmark`
- Events file: `data/events_v2.jsonl`
- Camera: `CAM_3`
- Tolerance: `0`
- Scenario coverage: entry, exit, and re-entry count validation

Current benchmark result:

| Camera | Event | Expected count | Predicted count | Absolute error | Error % |
| --- | ---: | ---: | ---: | ---: | ---: |
| `CAM_3` | `ENTRY` | 1 | 1 | 0 | 0.0% |
| `CAM_3` | `EXIT` | 1 | 1 | 0 | 0.0% |
| `CAM_3` | `REENTRY` | 1 | 1 | 0 | 0.0% |

Overall `entry_exit_reentry_accuracy`: `1.0`.

## End-to-End Pipeline Validation

`tests/test_pipeline_e2e.py` validates the video-to-event path without depending on a large model download during tests:

```text
generated MP4
  -> YOLO detector interface
  -> ByteTrack-compatible tracker path
  -> line crossing event builder
  -> JSONL event output
```

The test creates a two-frame MP4, patches the YOLO model loader with deterministic tracked boxes, runs `DetectionPipelineRunner`, and asserts that one structured `ENTRY` event is emitted with:

- `store_id`
- `camera_id`
- `event_type`
- ISO timestamp
- confidence
- source track ID
- crossing direction

SQLite ingestion and analytics API behavior are validated by the API, ingestion, metrics, funnel, heatmap, anomaly, POS attribution, and historical-window tests. Together these checks cover event generation, persistence, and read-only analytics responses, but the video E2E test itself intentionally stops at JSONL output.

## Quality Gates

Local gates:

```bash
ruff check .
pytest
python scripts/benchmark_detection.py --config data/detection_benchmark.json --json
```

The test suite enforces minimum coverage through `pyproject.toml`; the latest local run passed with 49 tests and 89.42% coverage. No CI workflow is included in this repository, so reviewers should run the local gates above.

## API Examples

Ingest one event:

```bash
curl -X POST http://localhost:8000/events/ingest ^
  -H "Content-Type: application/json" ^
  -d "{\"events\":[{\"event_id\":\"11111111-1111-4111-8111-111111111111\",\"store_id\":\"STORE_BLR_002\",\"camera_id\":\"CAM_ENTRY_01\",\"visitor_id\":\"VIS_001\",\"event_type\":\"ENTRY\",\"timestamp\":\"2026-06-01T12:00:00Z\",\"zone_id\":null,\"dwell_ms\":0,\"is_staff\":false,\"confidence\":0.91,\"metadata\":{\"queue_depth\":null,\"sku_zone\":null,\"session_seq\":1}}]}"
```

Query analytics:

```bash
curl http://localhost:8000/stores/STORE_BLR_002/metrics
curl http://localhost:8000/stores/STORE_BLR_002/funnel
curl http://localhost:8000/stores/STORE_BLR_002/heatmap
curl http://localhost:8000/stores/STORE_BLR_002/anomalies
curl http://localhost:8000/health
```

Historical windows are supported on analytics endpoints:

```bash
curl "http://localhost:8000/stores/STORE_BLR_002/metrics?start=2026-06-01T00:00:00Z&end=2026-06-02T00:00:00Z"
curl "http://localhost:8000/stores/STORE_BLR_002/funnel?start=2026-06-01T00:00:00Z&end=2026-06-02T00:00:00Z"
curl "http://localhost:8000/stores/STORE_BLR_002/heatmap?start=2026-06-01T00:00:00Z&end=2026-06-02T00:00:00Z"
curl "http://localhost:8000/stores/STORE_BLR_002/anomalies?start=2026-06-01T00:00:00Z&end=2026-06-02T00:00:00Z"
```

Sample metrics response:

```json
{
  "store_id": "STORE_BLR_002",
  "unique_visitors": 2,
  "conversion_rate": 0.5,
  "queue_depth": {
    "current_depth": 0,
    "max_depth": 2,
    "avg_wait_time": 50000.0,
    "peak_wait_time": 60000
  },
  "abandonment_rate": 0.0
}
```

Sample heatmap zone:

```json
{
  "zone_id": "SKINCARE",
  "visit_count": 2,
  "avg_dwell_ms": 15000.0,
  "heat_score": 100,
  "data_confidence": "LOW"
}
```

## Business Metrics

- **Unique visitors**: non-staff visitor sessions with an entry or re-entry event.
- **Conversion rate**: visitors with a purchase divided by total non-staff visitors.
- **Funnel**: sequential progression through `ENTRY -> ZONE_VISIT -> CASH_COUNTER -> PURCHASE`.
- **Zone dwell**: average, median, and max dwell per zone from zone enter/dwell/exit events.
- **Queue depth and wait**: derived from explicit queue events or `CASH_COUNTER` zone transitions.
- **Abandonment**: billing/queue sessions that do not result in purchase.
- **Anomalies**: high dwell, queue congestion, traffic spike, re-entry spike, low conversion, and POS mismatch.

## Running The Pipeline

To include the pipeline container in Docker Compose:

```bash
docker compose --profile pipeline up --build
```

Generate an event stream from a CCTV clip:

```bash
docker compose exec pipeline python -m pipeline.pipeline_runner \
  --video "/app/data/CCTV Footage/CAM 1.mp4" \
  --camera-id CAM_1 \
  --line-start 0,500 \
  --line-end 1920,500 \
  --frame-stride 5 \
  --output /app/data/events.jsonl
```

Replay generated events:

```bash
docker compose exec pipeline python /app/scripts/replay_events.py \
  --file /app/data/events.jsonl \
  --api-url http://api:8000
```

Load POS rows:

```bash
python scripts/load_pos.py --file data/pos_transactions.csv
```

## Assumptions

- Python 3.11 is the target runtime.
- The provided CCTV clips are processed on CPU by default.
- Store zones are defined by calibrated polygons in `data/zone_config.json`.
- Staff identification is supplied through `is_staff` and configurable staff zones such as `STAFF_ONLY`; the detector does not perform uniform or face-based staff classification.
- POS rows do not contain CCTV visitor IDs, so purchases are attributed by nearest non-staff session within a configurable time window.
- Analytics endpoints use the current UTC day when `start` and `end` are omitted, and support explicit historical windows through query parameters.

## Limitations

- CPU-only video inference can be slow on full CCTV clips.
- Cross-camera biometric re-identification is intentionally not implemented.
- The heatmap is zone-ranked data, not a rendered floor-plan overlay.
- There is no authentication or rate limiting; this is a challenge-scoped local deployment.
- SQLite is suitable for reproducible evaluation, not high-concurrency production writes.

## Troubleshooting

- If Docker build is slow, API and dashboard images should reuse cached dependency layers. The default Compose build uses service-specific Docker stages and does not build the pipeline image. Use `docker compose --profile pipeline up --build` only when the video pipeline container is needed.
- If `/health` is `DEGRADED`, the latest event is older than `STORE_INTEL_STALE_FEED_MINUTES`.
- If metrics are empty, ingest events for the same `store_id` and either query the matching historical window or use current UTC-day data.
- If POS purchases do not appear, check `STORE_INTEL_POS_CONVERSION_WINDOW_MINUTES`.
- If video cannot open, verify the path under `data/CCTV Footage/` and OpenCV runtime libraries.

## Future Improvements

- Add a real staff classifier beyond explicit `is_staff` flags and configured staff-only zones.
- Add cross-camera identity matching only after measuring false-merge risk.
- Move from SQLite to PostgreSQL for multi-writer deployments.
- Add authentication for ingest and dashboard access.
- Render a floor-plan heatmap overlay from zone polygons.
