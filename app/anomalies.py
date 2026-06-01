from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from app.config import get_settings
from app.models import AnomaliesResponse, Anomaly, AnomalySeverity, EventType
from app.repository import (
    EventRepository,
    VisitorSession,
    build_sessions,
    parse_ts,
    queue_analytics,
    zone_dwell,
)


@dataclass(frozen=True)
class ConversionSnapshot:
    visitors: int
    purchases: int

    @property
    def rate(self) -> float:
        return self.purchases / self.visitors if self.visitors else 0.0


class AnomalyService:
    """Rule-based production anomaly detector over persisted facts."""

    HIGH_DWELL_WARN_MS = 5 * 60 * 1000
    HIGH_DWELL_CRITICAL_MS = 10 * 60 * 1000
    QUEUE_DEPTH_WARN = 4
    QUEUE_DEPTH_CRITICAL = 8
    QUEUE_WAIT_WARN_MS = 3 * 60 * 1000
    QUEUE_WAIT_CRITICAL_MS = 5 * 60 * 1000
    TRAFFIC_WINDOW_MINUTES = 15
    TRAFFIC_SPIKE_MIN_ENTRIES = 5
    TRAFFIC_SPIKE_RATIO = 2.0
    REENTRY_WINDOW_MINUTES = 30
    REENTRY_SPIKE_MIN_COUNT = 3
    REENTRY_SPIKE_RATIO = 0.30
    LOW_CONVERSION_MIN_VISITORS = 5
    LOW_CONVERSION_WARN_RATE = 0.20
    LOW_CONVERSION_CRITICAL_RATE = 0.10
    POS_MISMATCH_WARN = 1
    POS_MISMATCH_CRITICAL = 5

    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository
        self.settings = get_settings()

    def anomalies_for_store(
        self,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AnomaliesResponse:
        all_rows = self.repository.events_for_store(store_id)
        pos_rows = self.repository.pos_transactions_for_store(store_id)
        if not all_rows and not pos_rows:
            return AnomaliesResponse(store_id=store_id, anomalies=[])

        anchor_time = _anchor_time(all_rows, pos_rows)
        day_start, day_end = _day_window(anchor_time)
        window_start = (start or day_start).astimezone(UTC)
        window_end = (end or day_end).astimezone(UTC)
        conversion_window = timedelta(minutes=self.settings.effective_pos_conversion_window_minutes)

        current_rows = self.repository.events_for_store(
            store_id,
            start=window_start,
            end=window_end,
        )
        current_pos = self.repository.pos_transactions_for_store(
            store_id,
            start=window_start,
            end=window_end,
        )
        current_sessions = build_sessions(
            current_rows,
            pos_rows=current_pos,
            conversion_window=conversion_window,
        )

        anomalies: list[Anomaly] = []
        anomalies.extend(self._high_dwell(current_rows, anchor_time))
        queue_anomaly = self._queue_congestion(current_rows, anchor_time)
        if queue_anomaly is not None:
            anomalies.append(queue_anomaly)
        traffic_anomaly = self._traffic_spike(current_rows, anchor_time)
        if traffic_anomaly is not None:
            anomalies.append(traffic_anomaly)
        reentry_anomaly = self._reentry_spike(current_rows, anchor_time)
        if reentry_anomaly is not None:
            anomalies.append(reentry_anomaly)
        conversion_anomaly = self._low_conversion(current_sessions, anchor_time)
        if conversion_anomaly is not None:
            anomalies.append(conversion_anomaly)
        pos_anomaly = self._pos_mismatch(current_pos, anchor_time)
        if pos_anomaly is not None:
            anomalies.append(pos_anomaly)

        return AnomaliesResponse(store_id=store_id, anomalies=anomalies)

    def _high_dwell(self, rows, timestamp: datetime) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        for zone_id, values in sorted(zone_dwell(rows).items()):
            dwell_values = values["dwell_values"]
            if not dwell_values:
                continue
            max_dwell_ms = max(dwell_values)
            if max_dwell_ms < self.HIGH_DWELL_WARN_MS:
                continue
            severity = (
                AnomalySeverity.CRITICAL
                if max_dwell_ms >= self.HIGH_DWELL_CRITICAL_MS
                else AnomalySeverity.WARN
            )
            anomalies.append(
                Anomaly(
                    type="HIGH_DWELL",
                    severity=severity,
                    timestamp=timestamp,
                    details={
                        "zone_id": zone_id,
                        "max_dwell_ms": max_dwell_ms,
                        "avg_dwell_ms": round(sum(dwell_values) / len(dwell_values), 2),
                        "visitor_count": len(values["visitors"]),
                        "threshold_ms": self.HIGH_DWELL_WARN_MS,
                    },
                )
            )
        return anomalies

    def _queue_congestion(self, rows, timestamp: datetime) -> Anomaly | None:
        queue = queue_analytics(rows)
        max_depth = int(queue["max_depth"])
        peak_wait_time = int(queue["peak_wait_time"])
        if max_depth < self.QUEUE_DEPTH_WARN and peak_wait_time < self.QUEUE_WAIT_WARN_MS:
            return None
        severity = (
            AnomalySeverity.CRITICAL
            if max_depth >= self.QUEUE_DEPTH_CRITICAL
            or peak_wait_time >= self.QUEUE_WAIT_CRITICAL_MS
            else AnomalySeverity.WARN
        )
        return Anomaly(
            type="QUEUE_CONGESTION",
            severity=severity,
            timestamp=timestamp,
            details={
                **queue,
                "depth_warn_threshold": self.QUEUE_DEPTH_WARN,
                "wait_warn_threshold_ms": self.QUEUE_WAIT_WARN_MS,
            },
        )

    def _traffic_spike(self, rows, timestamp: datetime) -> Anomaly | None:
        window = timedelta(minutes=self.TRAFFIC_WINDOW_MINUTES)
        current_start = timestamp - window
        previous_start = current_start - window
        current_count = _entry_count(rows, current_start, timestamp)
        previous_count = _entry_count(rows, previous_start, current_start)
        baseline = max(previous_count, 1)
        ratio = current_count / baseline
        if current_count < self.TRAFFIC_SPIKE_MIN_ENTRIES or ratio < self.TRAFFIC_SPIKE_RATIO:
            return None
        severity = AnomalySeverity.CRITICAL if ratio >= 3.0 else AnomalySeverity.WARN
        return Anomaly(
            type="TRAFFIC_SPIKE",
            severity=severity,
            timestamp=timestamp,
            details={
                "window_minutes": self.TRAFFIC_WINDOW_MINUTES,
                "current_entries": current_count,
                "previous_entries": previous_count,
                "spike_ratio": round(ratio, 2),
                "min_entries": self.TRAFFIC_SPIKE_MIN_ENTRIES,
            },
        )

    def _reentry_spike(self, rows, timestamp: datetime) -> Anomaly | None:
        window_start = timestamp - timedelta(minutes=self.REENTRY_WINDOW_MINUTES)
        reentries = {
            row["visitor_id"]
            for row in rows
            if row["event_type"] == EventType.REENTRY.value
            and parse_ts(row["timestamp"]) >= window_start
        }
        entries = {
            row["visitor_id"]
            for row in rows
            if row["event_type"] in {EventType.ENTRY.value, EventType.REENTRY.value}
            and parse_ts(row["timestamp"]) >= window_start
            and not row["is_staff"]
        }
        ratio = len(reentries) / max(len(entries), 1)
        if len(reentries) < self.REENTRY_SPIKE_MIN_COUNT or ratio < self.REENTRY_SPIKE_RATIO:
            return None
        severity = AnomalySeverity.CRITICAL if ratio >= 0.50 else AnomalySeverity.WARN
        return Anomaly(
            type="REENTRY_SPIKE",
            severity=severity,
            timestamp=timestamp,
            details={
                "window_minutes": self.REENTRY_WINDOW_MINUTES,
                "reentry_count": len(reentries),
                "entry_count": len(entries),
                "reentry_ratio": round(ratio, 4),
            },
        )

    def _low_conversion(
        self,
        sessions: dict[str, VisitorSession],
        timestamp: datetime,
    ) -> Anomaly | None:
        snapshot = _conversion_snapshot(sessions)
        if snapshot.visitors < self.LOW_CONVERSION_MIN_VISITORS:
            return None
        if snapshot.rate > self.LOW_CONVERSION_WARN_RATE:
            return None
        severity = (
            AnomalySeverity.CRITICAL
            if snapshot.rate <= self.LOW_CONVERSION_CRITICAL_RATE
            else AnomalySeverity.WARN
        )
        return Anomaly(
            type="LOW_CONVERSION",
            severity=severity,
            timestamp=timestamp,
            details={
                "visitors": snapshot.visitors,
                "purchases": snapshot.purchases,
                "conversion_rate": round(snapshot.rate, 4),
                "warn_threshold": self.LOW_CONVERSION_WARN_RATE,
                "critical_threshold": self.LOW_CONVERSION_CRITICAL_RATE,
            },
        )

    def _pos_mismatch(self, pos_rows, timestamp: datetime) -> Anomaly | None:
        unmatched = [
            row
            for row in pos_rows
            if row["matched_visitor_id"] is None
        ]
        if len(unmatched) < self.POS_MISMATCH_WARN:
            return None
        severity = (
            AnomalySeverity.CRITICAL
            if len(unmatched) >= self.POS_MISMATCH_CRITICAL
            else AnomalySeverity.WARN
        )
        brands = Counter((row["brand"] or "UNKNOWN") for row in unmatched)
        return Anomaly(
            type="POS_MISMATCH",
            severity=severity,
            timestamp=timestamp,
            details={
                "unmatched_transactions": len(unmatched),
                "sample_transaction_ids": [row["transaction_id"] for row in unmatched[:5]],
                "top_unmatched_brands": dict(brands.most_common(3)),
            },
        )


def _anchor_time(rows, pos_rows) -> datetime:
    timestamps = [parse_ts(row["timestamp"]) for row in rows]
    timestamps.extend(parse_ts(row["timestamp"]) for row in pos_rows)
    return max(timestamps) if timestamps else datetime.now(UTC)


def _day_window(anchor: datetime) -> tuple[datetime, datetime]:
    current = anchor.astimezone(UTC)
    start = datetime.combine(current.date(), time.min, tzinfo=UTC)
    end = datetime.combine(current.date(), time.max, tzinfo=UTC)
    return start, end


def _entry_count(rows, start: datetime, end: datetime) -> int:
    return len(
        {
            row["visitor_id"]
            for row in rows
            if row["event_type"] == EventType.ENTRY.value
            and start <= parse_ts(row["timestamp"]) <= end
            and not row["is_staff"]
        }
    )


def _conversion_snapshot(sessions: dict[str, VisitorSession]) -> ConversionSnapshot:
    customer_sessions = [
        session for session in sessions.values() if not session.is_staff and session.has_entry
    ]
    purchases = sum(1 for session in customer_sessions if session.has_purchase)
    return ConversionSnapshot(visitors=len(customer_sessions), purchases=purchases)
