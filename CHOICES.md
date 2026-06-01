# Implementation Choices

Each decision below documents the problem, options considered, final choice, why it was chosen, and tradeoffs accepted.

## 1. Why YOLO Was Chosen

**Problem:** Detect shoppers in CCTV frames with enough accuracy for downstream counting and dwell logic.

**Options considered:** OpenCV background subtraction, Haar/HOG detectors, YOLOv8, larger multi-model detectors.

**Final choice:** YOLOv8 nano through Ultralytics.

**Why chosen:** YOLOv8 is strong enough for person detection, easy to run locally, widely understood, and available with small weights. The challenge rewards a working event pipeline more than novel model architecture.

**Tradeoffs accepted:** It can miss small or occluded people and is slower on CPU than handcrafted frame differencing, but it is much more reliable than background subtraction in retail footage.

## 2. Why ByteTrack Was Chosen

**Problem:** Convert frame-level detections into stable visitor tracks.

**Options considered:** IoU-only tracking, SORT, ByteTrack, DeepSORT, custom re-identification.

**Final choice:** ByteTrack for production path, simple IoU tracker for deterministic unit tests.

**Why chosen:** ByteTrack handles low-confidence detections better than simple IoU matching and avoids the embedding model complexity of DeepSORT.

**Tradeoffs accepted:** Track IDs are not permanent identities. They are session identifiers within a camera/clip.

## 3. Why Not DeepSORT

**Problem:** Re-identification could help with occlusion and re-entry, but it can also merge unrelated shoppers.

**Options considered:** DeepSORT embeddings, cross-camera re-ID, conservative geometry-based re-entry.

**Final choice:** Do not use DeepSORT in the challenge build.

**Why chosen:** DeepSORT adds model dependencies, GPU pressure, calibration work, and privacy concerns. False identity merges would corrupt conversion and dwell metrics more severely than occasional missed re-entry.

**Tradeoffs accepted:** Some true re-entries may be counted as new sessions.

## 4. Why SQLite Was Chosen

**Problem:** Persist events and analytics facts without making reviewer setup difficult.

**Options considered:** In-memory store, JSONL only, SQLite, PostgreSQL.

**Final choice:** SQLite.

**Why chosen:** It provides real persistence, SQL semantics, indexes, idempotent inserts, and zero external service setup.

**Tradeoffs accepted:** SQLite is not ideal for high-concurrency ingestion. PostgreSQL would be the production upgrade.

## 5. Why FastAPI Was Chosen

**Problem:** Expose typed ingest and analytics endpoints quickly and reliably.

**Options considered:** Flask, FastAPI, Django REST Framework.

**Final choice:** FastAPI with Pydantic models.

**Why chosen:** FastAPI gives request validation, OpenAPI docs, simple routing, and strong test ergonomics.

**Tradeoffs accepted:** The app is synchronous around SQLite. That is acceptable for challenge scale.

## 6. Why Streamlit Was Chosen

**Problem:** Provide a reviewer-visible dashboard without building a full frontend stack.

**Options considered:** Static screenshots, React app, Streamlit.

**Final choice:** Streamlit.

**Why chosen:** It starts quickly, works in Docker, and lets reviewers inspect live API-backed metrics.

**Tradeoffs accepted:** It is less polished than a custom frontend and does not render a true floor-plan heatmap.

## 7. Re-entry Strategy

**Problem:** A visitor can exit and return, but permanent identity is hard to prove from CCTV.

**Options considered:** Treat every new track as new visitor, biometric re-ID, conservative session matching.

**Final choice:** Match recent exits to new tracks using time window, position, and size similarity.

**Why chosen:** It is explainable and avoids overclaiming identity.

**Tradeoffs accepted:** Conservative matching misses some real re-entries.

## 8. Staff Handling Strategy

**Problem:** Staff should not inflate customer metrics.

**Options considered:** Ignore staff, detect uniforms, manually tag through events.

**Final choice:** Carry `is_staff` through the event schema and exclude staff in analytics.

**Why chosen:** The API and analytics remain correct when staff labels exist, while avoiding a weak untrained staff classifier.

**Tradeoffs accepted:** The video pipeline does not infer staff automatically.

## 9. Group Entry Strategy

**Problem:** Multiple shoppers can cross the line together.

**Options considered:** Count one crossing per frame, count one per track, estimate groups by blob size.

**Final choice:** Count entry/exit per tracked person.

**Why chosen:** Per-track events preserve individual sessions and prevent a group from collapsing into one visitor.

**Tradeoffs accepted:** Heavy occlusion can still hide one member of a group.

## 10. Occlusion Handling Strategy

**Problem:** Retail CCTV often has temporary occlusion.

**Options considered:** Ignore occlusion, DeepSORT embeddings, ByteTrack with conservative recovery.

**Final choice:** Use ByteTrack and avoid aggressive identity recovery.

**Why chosen:** ByteTrack is a good middle ground for short occlusions without adding a second model.

**Tradeoffs accepted:** Long occlusions can split tracks.

## 11. Session Attribution Strategy

**Problem:** POS transactions do not contain CCTV visitor IDs.

**Options considered:** Count every POS row as conversion, require exact visitor ID, match nearest non-staff session.

**Final choice:** Attribute POS rows to nearest non-staff visitor session within a configurable time window.

**Why chosen:** It is deterministic, auditable, and produces purchase events that the same funnel and metrics logic can consume.

**Tradeoffs accepted:** Crowded checkout periods can be ambiguous; the attribution confidence records that uncertainty.

## 12. Why Certain Approaches Were Rejected

**Problem:** Challenge systems often look impressive but fail review because they are hard to run or hard to verify.

**Options considered:** Kafka, PostgreSQL, Redis, GPU runtime, VLM zone interpretation, biometric re-ID.

**Final choice:** Keep the build local, CPU-capable, SQLite-backed, and event-driven.

**Why chosen:** The evaluator can run it with Docker Compose and inspect real data flow.

**Tradeoffs accepted:** This is not a fully distributed production deployment.

## 13. Production Tradeoffs

**Problem:** Balance reviewer reproducibility with production-shaped design.

**Options considered:** Minimal demo app, full cloud architecture, local production-shaped app.

**Final choice:** Local production-shaped app.

**Why chosen:** It demonstrates validation, idempotency, persistence, health, logging, and tests without requiring external services.

**Tradeoffs accepted:** Authentication, rate limiting, durable queues, and OpenTelemetry are future work.

## 14. Known Limitations

**Problem:** Reviewers should know what is intentionally not solved.

**Options considered:** Hide limitations, document them directly.

**Final choice:** Document limitations directly.

**Why chosen:** Honest limitations increase confidence that the implementation is understood.

**Tradeoffs accepted:** This may reduce perceived ambition, but avoids overclaiming.

Known limitations:

- CPU-only full-video inference can be slow.
- Staff classification is schema-supported but not visually inferred.
- Cross-camera identity is not implemented.
- Analytics endpoints currently focus on current-day windows.
- Heatmap is a ranked zone view, not a floor-plan overlay.

## 15. Future Roadmap

**Problem:** Show how the challenge build can become production-grade.

**Options considered:** Add features randomly, prioritize by risk.

**Final choice:** Prioritize correctness, observability, and scalability.

**Why chosen:** Store operations need trustworthy metrics before advanced automation.

**Tradeoffs accepted:** Some advanced CV features remain future work.

Roadmap:

1. Add time-window query parameters to analytics endpoints.
2. Add staff classification or staff schedule integration.
3. Add floor-plan heatmap rendering.
4. Add PostgreSQL and migrations.
5. Add authenticated ingest and dashboard access.
6. Add OpenTelemetry and alert routing.
7. Evaluate cross-camera re-ID only after measuring false merge rate.
