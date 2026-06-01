from __future__ import annotations

from datetime import datetime

from app.metrics import resolve_window
from app.models import HeatmapResponse, HeatmapZone
from app.repository import EventRepository, build_sessions, zone_dwell


class HeatmapService:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def heatmap_for_store(
        self,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> HeatmapResponse:
        start, end = resolve_window(start, end)
        rows = self.repository.events_for_store(store_id, start=start, end=end)
        sessions = [
            session
            for session in build_sessions(rows).values()
            if not session.is_staff and session.has_entry
        ]
        confidence = "HIGH" if len(sessions) >= 20 else "LOW"
        zones = zone_dwell(rows)
        max_visits = max((values["visits"] for values in zones.values()), default=0)
        max_dwell = max((values["dwell"] for values in zones.values()), default=0)

        heatmap_zones: list[HeatmapZone] = []
        for zone_id, values in sorted(zones.items()):
            visit_score = values["visits"] / max(max_visits, 1)
            dwell_score = values["dwell"] / max(max_dwell, 1)
            heat_score = round(((visit_score * 0.6) + (dwell_score * 0.4)) * 100)
            heatmap_zones.append(
                HeatmapZone(
                    zone_id=zone_id,
                    visit_count=values["visits"],
                    avg_dwell_ms=round(values["dwell"] / max(values["visits"], 1), 2),
                    heat_score=max(0, min(100, heat_score)),
                    data_confidence=confidence,
                )
            )

        return HeatmapResponse(store_id=store_id, zones=heatmap_zones)
