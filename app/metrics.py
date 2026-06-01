from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from statistics import median

from app.config import get_settings
from app.models import MetricsResponse, QueueDepthMetric, Window, ZoneDwellMetric
from app.repository import EventRepository, build_sessions, queue_analytics, zone_dwell


def today_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    start = datetime.combine(current.date(), time.min, tzinfo=UTC)
    end = datetime.combine(current.date(), time.max, tzinfo=UTC)
    return start, end


def resolve_window(
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[datetime, datetime]:
    default_start, default_end = today_window()
    return (
        (start or default_start).astimezone(UTC),
        (end or default_end).astimezone(UTC),
    )


class MetricsService:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def metrics_for_store(
        self,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> MetricsResponse:
        settings = get_settings()
        start, end = resolve_window(start, end)
        conversion_window = timedelta(minutes=settings.effective_pos_conversion_window_minutes)
        rows = self.repository.events_for_store(store_id, start=start, end=end)
        pos_rows = self.repository.pos_transactions_for_store(store_id, start=start, end=end)
        sessions = build_sessions(
            rows,
            pos_rows=pos_rows,
            conversion_window=conversion_window,
        )
        customer_sessions = [
            session for session in sessions.values() if not session.is_staff and session.has_entry
        ]

        unique_visitors = len(customer_sessions)
        converted = sum(1 for session in customer_sessions if session.has_purchase)
        conversion_rate = round(converted / unique_visitors, 4) if unique_visitors else 0.0

        dwell_by_zone = zone_dwell(rows)
        avg_dwell_per_zone = [
            ZoneDwellMetric(
                zone_id=zone_id,
                avg_dwell_ms=round(values["dwell"] / max(len(values["dwell_values"]), 1), 2),
                median_dwell_ms=round(float(median(values["dwell_values"])), 2)
                if values["dwell_values"]
                else 0.0,
                max_dwell_ms=max(values["dwell_values"], default=0),
                visitor_count=len(values["visitors"]),
                repeat_visitor_count=sum(
                    1 for visit_count in values["visits_by_visitor"].values() if visit_count > 1
                ),
            )
            for zone_id, values in sorted(dwell_by_zone.items())
        ]

        queue_values = queue_analytics(rows)
        queue_depth = QueueDepthMetric(
            current_depth=queue_values["current_depth"],
            max_depth=queue_values["max_depth"],
            avg_wait_time=queue_values["avg_wait_time"],
            peak_wait_time=queue_values["peak_wait_time"],
            current=queue_values["current_depth"],
            max=queue_values["max_depth"],
        )

        billing_sessions = [session for session in customer_sessions if session.has_billing]
        abandoned = sum(
            1
            for session in billing_sessions
            if session.has_abandonment and not session.has_purchase
        )
        abandonment_rate = round(abandoned / len(billing_sessions), 4) if billing_sessions else 0.0

        return MetricsResponse(
            store_id=store_id,
            window=Window(start=start, end=end),
            unique_visitors=unique_visitors,
            conversion_rate=conversion_rate,
            avg_dwell_per_zone=avg_dwell_per_zone,
            queue_depth=queue_depth,
            abandonment_rate=abandonment_rate,
        )
