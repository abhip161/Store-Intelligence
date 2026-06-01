from __future__ import annotations

from datetime import datetime, timedelta

from app.config import get_settings
from app.metrics import resolve_window
from app.models import FunnelResponse, FunnelStage
from app.repository import EventRepository, build_sessions

FUNNEL_STAGES = ("ENTRY", "ZONE_VISIT", "CASH_COUNTER", "PURCHASE")


class FunnelService:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def funnel_for_store(
        self,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> FunnelResponse:
        settings = get_settings()
        start, end = resolve_window(start, end)
        conversion_window = timedelta(minutes=settings.effective_pos_conversion_window_minutes)
        rows = self.repository.events_for_store(store_id, start=start, end=end)
        pos_rows = self.repository.pos_transactions_for_store(store_id, start=start, end=end)
        sessions = build_sessions(rows, pos_rows=pos_rows, conversion_window=conversion_window)

        entry_visitors = {
            visitor_id
            for visitor_id, session in sessions.items()
            if not session.is_staff and session.has_entry
        }
        zone_visitors = {
            visitor_id
            for visitor_id in entry_visitors
            if sessions[visitor_id].has_zone_visit
        }
        cash_counter_visitors = {
            visitor_id
            for visitor_id in zone_visitors
            if sessions[visitor_id].has_billing
        }
        purchase_visitors = {
            visitor_id
            for visitor_id in cash_counter_visitors
            if sessions[visitor_id].has_purchase
        }

        counts = {
            "ENTRY": len(entry_visitors),
            "ZONE_VISIT": len(zone_visitors),
            "CASH_COUNTER": len(cash_counter_visitors),
            "PURCHASE": len(purchase_visitors),
        }

        stages: list[FunnelStage] = []
        previous_count: int | None = None
        entry_count = counts["ENTRY"]
        for stage in FUNNEL_STAGES:
            count = counts[stage]
            if previous_count is None or previous_count == 0:
                dropoff_pct = 0.0
            else:
                dropoff_pct = round(((previous_count - count) / previous_count) * 100, 2)
            conversion_pct = round((count / entry_count) * 100, 2) if entry_count else 0.0
            stages.append(
                FunnelStage(
                    stage=stage,
                    count=count,
                    dropoff_pct=max(0.0, dropoff_pct),
                    conversion_pct=conversion_pct,
                )
            )
            previous_count = count

        return FunnelResponse(store_id=store_id, stages=stages)
