# DESIGN.md — Store Intelligence System

## Overview

Retail stores need to understand foot traffic — who walks in, how long they stay, which zones they visit, whether they buy anything. This system turns CCTV footage into structured visitor events and serves analytics through an API.

YOLOv8n finds people in frames, ByteTrack assigns track IDs, a line-crossing engine determines entry/exit, and a zone engine tracks dwell time. Each state change becomes a typed event in JSONL. Events are ingested into SQLite via FastAPI, feeding metrics like unique visitors, conversion rates, zone dwell, and funnel progression. A Streamlit dashboard polls the API and renders live.

The detector can be swapped without touching the API; analytics can evolve without retraining any model.

---

## System Architecture

```
┌──────────────┐
│  CCTV Video  │
└──────┬───────┘
       ▼
┌──────────────────┐     ┌──────────────────┐
│  YOLOv8n         │────▶│  ByteTrack        │
│  Person Detector │     │  Tracker          │
└──────────────────┘     └────────┬─────────┘
                                  │
                    ┌─────────────┼──────────────┐
                    ▼             ▼              ▼
             ┌───────────┐ ┌───────────┐ ┌────────────┐
             │ Line      │ │ Zone      │ │ Re-entry   │
             │ Crossing  │ │ Engine    │ │ Matching   │
             └─────┬─────┘ └─────┬─────┘ └──────┬─────┘
                   └──────────┬──┘───────────────┘
                              ▼
                    ┌──────────────────┐
                    │  Event Builder   │
                    │  (JSONL output)  │
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │  FastAPI         │
                    │  POST /events/   │
                    │  ingest          │
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │  SQLite          │
                    │  (events, zones, │
                    │   queues, POS)   │
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │  Analytics APIs  │
                    │  /metrics /funnel│
                    │  /heatmap /health│
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │  Streamlit       │
                    │  Dashboard       │
                    └──────────────────┘
```

---

## Key Design Decisions

### 1. YOLOv8n + ByteTrack for Detection and Tracking

I chose YOLOv8n (nano) because the system runs on CPU and I needed 20+ fps. YOLOv8s gets 12–15 fps; RT-DETR dropped to ~2 fps, which was a non-starter.

ByteTrack handles multi-object tracking via Ultralytics' `model.track(tracker="bytetrack.yaml")`. I set `conf=0.4` as track activation threshold after finding that lower values generated phantom tracks from reflections and shadows near doorways.

The alternative was DeepSORT, which uses appearance features for re-ID. ByteTrack is motion-only (IoU-based) — faster and no separate re-ID model needed. The tradeoff: ByteTrack struggles with long occlusions. For entry/exit counting at a doorway, that's acceptable.

### 2. Schema Normalization Over Strict Validation

Sample event data used different field names than the spec — `id_token` vs `visitor_id`, `event_timestamp` vs `timestamp`. Instead of rejecting non-conforming events, I built a normalization layer mapping ~15 known aliases to a canonical schema.

Each event is validated individually through Pydantic after normalization. Invalid events get rejected with structured errors; one bad event in a batch of 500 doesn't block the other 499.

### 3. Session-Based Analytics from Raw Events

Metrics are derived from visitor sessions reconstructed at query time via `build_sessions()`. Sessions handle deduplication (ENTRY + REENTRY = one session), staff exclusion, and purchase attribution (POS transactions matched within a 5-minute window).

This keeps the event stream as the single source of truth. The cost: every metrics request re-scans events. For the evaluation dataset, that's single-digit milliseconds. It would need materialized aggregates at higher volume.

---

## What Works Well

- **Entry/exit line crossing** — Foot-point detection with a dead-band epsilon prevents jitter-induced duplicate events.
- **Session deduplication** — `VisitorIdManager` merges tracks for returning visitors using position proximity (≤180px) and bbox size ratio (≥0.45).
- **Idempotent ingestion** — Duplicate `event_id` values silently ignored via `INSERT OR IGNORE`. Replaying JSONL never inflates counts.
- **Per-event error handling** — Malformed events rejected individually with index and event_id.
- **Dashboard honesty** — Streamlit reads only from public API endpoints. No direct SQLite reads, no mock data.

---

## Known Limitations

- **Group detection** — Two or three people walking through a doorway together sometimes merge into one bounding box. The system counts them as one visitor. No reliable way to split merged detections on CPU.
- **Staff classification** — Staff identified only by zone presence (entering STAFF_ONLY or BACK_OFFICE). No appearance-based classifier. A staff member who never enters those zones counts as a customer.
- **Re-entry matching** — The heuristic (center distance ≤180px, size ratio ≥0.45) is tuned for the test footage. Different camera angles and resolutions would need different thresholds. False negatives preferred over false positives.
- **CPU performance** — YOLOv8n at `imgsz=640` processes 25–30 fps on CPU. Adequate for clip-based processing, not for real-time multi-camera feeds.
- **Single-camera tracking** — No cross-camera identity matching. A visitor entering via Camera A and exiting via Camera B appears as two visitors.

---

## AI-Assisted Decisions

### 1. ByteTrack vs DeepSORT

I asked AI which tracker to pair with YOLOv8 for retail counting. AI recommended ByteTrack — motion-based tracking (IoU matching) is sufficient for doorway counting and appearance features in DeepSORT would add latency without meaningful accuracy gains on CPU.

I agreed. DeepSORT needs a separate re-ID CNN, slowing the pipeline. ByteTrack's two-stage association fits doorway scenarios where people move predictably.

I adjusted thresholds after testing: `track_activation_threshold=0.4` (up from 0.25) because low-confidence detections near glass doors created phantom tracks, and `lost_track_buffer=90` frames so tracks survive brief occlusions. The default of 30 was splitting single visits into multiple tracks.

### 2. Schema Design

AI recommended strict schema validation — define the exact shape, reject non-conforming data. That's the textbook answer when you control producers.

I rejected it because the challenge data itself didn't conform. Fields like `visitor_id` appeared as `id_token`. Strict validation would reject the data I was supposed to ingest. I built normalization mapping known aliases to canonical fields before validation.

### 3. SQLite vs PostgreSQL

AI recommended PostgreSQL for write concurrency and production readiness. That's correct for a deployed multi-store system.

I chose SQLite because operational simplicity mattered more here. No server process, no connection pooling, no credential management, no Docker networking between containers. The database is one file — inspectable, deletable, rebuildable in seconds.

The tradeoff is real: limited write concurrency, no replication, no role-based access. For the evaluation dataset with batch ingestion and read-heavy analytics, those limits don't surface. For 40+ stores with continuous ingestion, I'd move to PostgreSQL, add pgbouncer, and run session materialization as a background job.

---

## Lessons Learned

- **Tracking errors dominated accuracy more than detector quality.** I compared YOLOv8n vs YOLOv8s mAP numbers, but the actual accuracy problem was phantom tracks from low-confidence detections and track fragmentation from aggressive lost-track timeouts. Fixing tracker config (`conf=0.4`, `lost_track_buffer=90`) had a bigger impact on event quality than any model swap would have.

- **I would redesign purchase attribution.** Currently POS transactions match to the nearest non-staff session within a 5-minute window using time proximity alone. During busy periods, this can attribute a purchase to the wrong visitor. A better design would weight zone-level signals — if a visitor was seen in the CASH_COUNTER zone within 60 seconds of the transaction, that match should rank higher.

- **Idempotent ingestion worked better than expected.** `INSERT OR IGNORE` with event_id as the uniqueness key meant I could replay the full JSONL during development without worrying about duplicate counts. I'd initially thought I'd need a separate deduplication step, but making the insert itself idempotent covered every replay case I encountered.
