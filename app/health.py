from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.models import HealthResponse, StoreHealth
from app.repository import EventRepository


class HealthService:
    def __init__(self, repository: EventRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    def health(self) -> HealthResponse:
        now = datetime.now(UTC)
        last_events = self.repository.all_store_last_events()
        stores: list[StoreHealth] = []
        has_stale_feed = False
        for store_id, last_event in sorted(last_events.items()):
            lag_seconds = int((now - last_event).total_seconds())
            feed_status = (
                "STALE_FEED"
                if lag_seconds > self.settings.stale_feed_minutes * 60
                else "OK"
            )
            has_stale_feed = has_stale_feed or feed_status == "STALE_FEED"
            stores.append(
                StoreHealth(
                    store_id=store_id,
                    last_event_timestamp=last_event,
                    feed_status=feed_status,
                    lag_seconds=lag_seconds,
                )
            )
        return HealthResponse(
            status="DEGRADED" if has_stale_feed else "OK",
            database="OK",
            stores=stores,
        )
