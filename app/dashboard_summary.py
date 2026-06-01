from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.metrics import resolve_window
from app.models import DashboardSummaryResponse, TrendPoint, Window
from app.repository import EventRepository, parse_ts


class DashboardSummaryService:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def summary_for_store(
        self,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> DashboardSummaryResponse:
        start, end = resolve_window(start, end)
        rows = self.repository.events_for_store(store_id, start=start, end=end)
        hourly: dict[datetime, dict[str, set[str]]] = defaultdict(
            lambda: {"visitors": set(), "purchases": set()}
        )

        for row in rows:
            if row["is_staff"]:
                continue
            event_type = row["event_type"]
            if event_type not in {"ENTRY", "REENTRY", "PURCHASE"}:
                continue
            bucket = _hour_bucket(parse_ts(row["timestamp"]))
            if event_type in {"ENTRY", "REENTRY"}:
                hourly[bucket]["visitors"].add(row["visitor_id"])
            elif event_type == "PURCHASE":
                hourly[bucket]["purchases"].add(row["visitor_id"])

        points: list[TrendPoint] = []
        for timestamp, values in sorted(hourly.items()):
            visitors = len(values["visitors"])
            purchases = len(values["purchases"])
            points.append(
                TrendPoint(
                    timestamp=timestamp,
                    visitors=visitors,
                    purchases=purchases,
                    conversion_rate=round(purchases / visitors, 4) if visitors else 0.0,
                )
            )

        return DashboardSummaryResponse(
            store_id=store_id,
            window=Window(start=start, end=end),
            visitor_trend=points,
            conversion_trend=points,
        )


def _hour_bucket(timestamp: datetime) -> datetime:
    return timestamp.replace(minute=0, second=0, microsecond=0)
