# CHOICES.md — Engineering Decisions

## Decision 1: Detection Model — YOLOv8n + ByteTrack

### What I was choosing between

- **YOLOv8n** — ~3.2M parameters, runs 25–30 fps on CPU at `imgsz=640`. Good enough for person detection in fixed-angle CCTV.
- **YOLOv8s** — Roughly 2× the parameters. Gets 12–15 fps on CPU. Slightly better mAP on COCO, but that gap doesn't translate cleanly to fixed retail footage where people are mostly upright and mid-frame.
- **RT-DETR** — Transformer-based. Better at crowded scenes in theory, around 2 fps on CPU in practice.
- **MediaPipe** — Fast pose estimation, but designed for single-person close-up. No bounding boxes for multi-person crowds, no tracking pipeline.
- **Vision-Language Models (CLIP/OWL-ViT)** — Too slow for frame-by-frame processing on CPU, and I don't need semantic scene understanding. I need bounding boxes and track IDs.

### What AI suggested

AI recommended YOLOv8n for CPU-only retail analytics, noting that for counting visitors at a store entrance, nano's accuracy gap versus the small variant wouldn't matter much. That was a reasonable call.

### What I chose and why

YOLOv8n at `imgsz=640`, `conf=0.4` as the effective track activation threshold, ByteTrack with `lost_track_buffer=90` frames. The entire pipeline is CPU-only — no GPU dependency in Docker.

I'm counting visitors and building sessions, not detecting fine-grained attributes. For entry/exit counting with line crossing, I need a bounding box that's roughly right and a track ID that's stable across frames. YOLOv8n delivers that.

### What surprised me

The confidence threshold mattered far more than model size. With the default `conf=0.25`, I got a flood of low-confidence detections near doorways — reflections, partial limbs, shadows. These created short-lived tracks that crossed the entry line and generated false ENTRY events. Bumping threshold to 0.4 for track activation cleaned up most of that noise. I'd assumed I needed a bigger model for better accuracy, but the real fix was filtering garbage detections before they became garbage tracks.

### What I rejected and why

RT-DETR was the most interesting alternative. At ~2 fps on CPU, processing a 60-second clip would take 7–8 minutes. That makes iterative development painful and demo startup unacceptable. With a GPU requirement it might work, but the constraint was CPU-only.

---

## Decision 2: Event Schema — Flexible Normalization vs Strict Validation

### The problem I discovered

The sample event data used different field names than the problem description: `id_token` vs `visitor_id`, `event_timestamp` vs `timestamp`, `store_code` vs `store_id`. This wasn't a bug — it reflected what real-world retail data looks like when multiple systems emit events.

### Options considered

**Option A: Strict schema enforcement.** One canonical shape. Reject non-conforming events.

**Option B: Flexible normalization layer.** Accept known aliases, map to canonical fields internally.

### What AI suggested

AI preferred strict schema consistency — define the contract, reject everything else, make producers conform.

### Why I disagreed

Strict validation would reject valid real-world data. The challenge data itself demonstrated schema variation — the very data I was supposed to ingest didn't match a single strict schema.

### What I built

A canonical schema in Pydantic (`EventIn`) with the fields I need, plus a normalization layer handling roughly 15 field aliases. `EventMetadata` uses `extra="allow"` so unexpected fields pass through without breaking ingestion. Events that fail validation after normalization are rejected per-record with structured errors — one bad event doesn't poison the batch.

### What I would do differently in production

Schema versioning — a `schema_version` field so the normalization layer knows which mapping to apply. A migration strategy for upgrading producers incrementally. Stronger contracts so the alias list doesn't grow forever. The current approach works for a bounded evaluation dataset but would become maintenance burden with 10+ event producers.

---

## Decision 3: Session Engine Design — Lazy Rebuild vs On-the-Fly Aggregation

### What I was choosing between

**Option A:** Compute everything from raw events at query time.

**Option B:** Materialize sessions into a table on ingest.

### What AI suggested

AI recommended on-the-fly computation because the dataset was small — hundreds to low thousands of events per store.

### Why I partially agreed

For the evaluation dataset, on-the-fly computation takes single-digit milliseconds. But pure event scanning misses session semantics — a visitor who leaves briefly for a phone call and returns should be one session, not two.

### What I actually built

A hybrid. `build_sessions()` in `app/repository.py` reconstructs visitor sessions from raw events at query time. It handles deduplication (ENTRY + REENTRY = one session), re-entry (extending sessions within the 120-second re-entry timeout window), and staff exclusion (visitors in STAFF_ONLY zones are flagged). The POS conversion window is 5 minutes — purchases are attributed to the nearest non-staff session within that window.

Sessions are rebuilt lazily per query. No background job, no materialized table, no cache invalidation. The event stream is the source of truth.

### What breaks at scale

Around 40+ stores with continuous event streams, scanning raw events per request would introduce noticeable latency. The fix: PostgreSQL, a background job materializing session aggregates on a schedule, and serving metrics from the materialized table. I chose not to build that because it adds infrastructure (task queue, background worker, cache invalidation) I couldn't adequately test with the evaluation dataset.
