from __future__ import annotations

import uuid
from datetime import datetime

from pipeline.schemas import EventMetadata, EventType, StoreEvent


class EventBuilder:
    def __init__(self, *, store_id: str, camera_id: str) -> None:
        self.store_id = store_id
        self.camera_id = camera_id

    def build(
        self,
        *,
        visitor_id: str,
        event_type: EventType,
        timestamp: datetime,
        confidence: float,
        is_staff: bool,
        zone_id: str | None = None,
        dwell_ms: int = 0,
        queue_depth: int | None = None,
        sku_zone: str | None = None,
        session_seq: int | None = None,
        track_id: int | None = None,
        extra_metadata: dict[str, object] | None = None,
    ) -> StoreEvent:
        return StoreEvent(
            event_id=str(uuid.uuid4()),
            store_id=self.store_id,
            camera_id=self.camera_id,
            visitor_id=visitor_id,
            event_type=event_type,
            timestamp=timestamp,
            zone_id=zone_id,
            dwell_ms=max(0, int(dwell_ms)),
            is_staff=is_staff,
            confidence=max(0.0, min(1.0, float(confidence))),
            metadata=EventMetadata(
                queue_depth=queue_depth,
                sku_zone=sku_zone,
                session_seq=session_seq,
                track_id=track_id,
                extra=extra_metadata or {},
            ),
        )
