# Implementation Choices

## Detection Approach

Alternatives considered:

- OpenCV background subtraction and contour filtering.
- YOLOv8 or another modern detector with tracking.
- Heavy multi-model detector plus re-identification stack.
- Vision-language interpretation of store zones.

AI suggestions:

AI initially favored adding more learned components for staff detection, identity matching, and semantic zone interpretation.

Final decision:

Use YOLOv8 for person detection and ByteTrack for tracking, with deterministic test doubles for CI.

Reasoning:

YOLOv8 and ByteTrack are standard, explainable, and strong enough for challenge-scale CCTV. The scoring emphasis is not model novelty; it is whether detections become correct business events. A heavier stack would be harder to debug and defend.

Tradeoffs:

The approach may miss some cross-camera identity continuity, but it avoids dangerous false merges and keeps the pipeline maintainable.

Rejected:

Background subtraction was too brittle for crowded retail footage. Vision-language zone inference was rejected because explicit calibrated polygons are more auditable.

## Tracking Approach

Alternatives considered:

- Count raw detections per frame.
- Track per camera only.
- Add cross-camera biometric re-identification.
- Use conservative session matching for re-entry.

AI suggestions:

AI suggested a more ambitious re-identification layer. That would sound impressive, but it would be difficult to validate under challenge constraints.

Final decision:

Track per camera, use line crossing for entry/exit, and use `VisitorIdManager` for conservative re-entry matching.

Reasoning:

The system needs stable visit sessions, not permanent customer identity. Per-track session state supports group entry, entry/exit, re-entry, and event sequencing while keeping false merges under control.

Tradeoffs:

Some true re-entries may become new visitors if the geometry is not similar enough. That is preferable to merging unrelated shoppers and corrupting conversion metrics.

Rejected:

Counting raw detections was rejected because it cannot handle dwell, re-entry, or duplicate visitors. Full biometric identity was rejected as unnecessary and privacy-sensitive.

## Storage Choice

Alternatives considered:

- In-memory storage.
- JSONL-only storage.
- SQLite.
- PostgreSQL.

AI suggestions:

AI suggested a production database plus asynchronous projections. That is a good later architecture, but too heavy for a five-minute reviewer startup.

Final decision:

Use SQLite with explicit schema, indexes, and idempotent inserts.

Reasoning:

SQLite gives real persistence, simple Docker operation, deterministic tests, and no external setup. It is enough for the challenge dataset and easy to inspect.

Tradeoffs:

SQLite has limited write concurrency compared with PostgreSQL. For this evaluation, correctness and reproducibility are more valuable than distributed scale.

Rejected:

In-memory storage was rejected because replay and dashboard behavior would be fragile. JSONL-only storage was rejected because analytics need indexed queries and idempotent mutation.

## API Architecture

Alternatives considered:

- Single-file FastAPI app.
- Routers plus service modules plus repository.
- ORM-heavy layered architecture.
- Fully asynchronous event bus and projections.

AI suggestions:

AI leaned toward queues and projections. The final choice kept the production shape but removed unnecessary moving parts.

Final decision:

Use FastAPI routers, Pydantic validation, service modules, and an explicit SQLite repository.

Reasoning:

This keeps the API easy to test and review. Each endpoint maps to a scoring category, and SQL stays out of route handlers.

Tradeoffs:

Analytics are computed synchronously, which is simpler but would need materialized aggregates at high event volume.

Rejected:

The one-file approach was rejected for maintainability. A queue-first architecture was rejected because it increases acceptance-gate risk without improving evaluation correctness.

## Analytics Design

Alternatives considered:

- Hardcoded demo metrics.
- Precomputed aggregate tables only.
- Query persisted events on demand.
- Hybrid event stream with derived visit tables.

AI suggestions:

AI correctly suggested edge cases such as duplicate events, all-staff clips, empty stores, and re-entry. It also suggested broader aggregate projections than this challenge needs.

Final decision:

Compute metrics, funnel, heatmap, and anomalies from persisted events, while persisting `zone_visits`, `queue_visits`, and `purchase_attributions` for auditability.

Reasoning:

The evaluator can change input events and see outputs change naturally. Idempotent replay does not inflate counts. Staff exclusion and sequential funnel rules are explicit.

Tradeoffs:

On-demand computation is less scalable than precomputed projections but simpler and more trustworthy for bounded datasets.

Rejected:

Hardcoded outputs and mock dashboards were rejected because they would fail evaluation scrutiny. Aggregate-only storage was rejected because it hides the facts behind each metric.

## Purchase Attribution

Alternatives considered:

- Treat POS rows as direct purchases without visitor matching.
- Require exact visitor ID in POS.
- Match nearest non-staff session within a configured time window.

AI suggestions:

AI suggested probabilistic matching. The implemented version keeps a deterministic confidence score instead.

Final decision:

Attribute each unattributed POS row to the nearest non-staff visitor session within `STORE_INTEL_POS_CONVERSION_WINDOW_MINUTES`.

Reasoning:

The POS dataset does not naturally carry CCTV visitor IDs. Time-window attribution is simple, explainable, and idempotent. It generates a `PURCHASE` event so metrics and funnel share one signal.

Tradeoffs:

Attribution may be ambiguous during crowded billing periods. The confidence score makes that uncertainty visible.

Rejected:

Exact matching was rejected because the input data does not support it. Blind conversion counting was rejected because it would fabricate visitor-level conversion.

## Dashboard Design

Alternatives considered:

- Static screenshots.
- Dashboard reading SQLite directly.
- Dashboard polling public APIs.

AI suggestions:

AI suggested rich panels and empty states. The important constraint was to avoid mock data.

Final decision:

Use Streamlit to poll the FastAPI endpoints and render live metrics, trend, funnel, heatmap, queue, anomaly, and health views.

Reasoning:

API-only dashboard behavior proves the backend contract works and keeps the UI honest. It also starts quickly in Docker.

Tradeoffs:

Streamlit is less custom than a dedicated frontend, but it is fast, reliable, and appropriate for challenge review.

Rejected:

Static or mocked dashboard content was rejected because it would not demonstrate end-to-end functionality.
