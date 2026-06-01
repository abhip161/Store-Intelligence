from __future__ import annotations

import json
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db import get_connection
from app.models import EventIn, EventType

QUEUE_ZONE_ID = "CASH_COUNTER"


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class DatabaseUnavailable(RuntimeError):
    pass


@dataclass
class VisitorSession:
    visitor_id: str
    is_staff: bool
    first_seen: datetime
    last_seen: datetime
    has_entry: bool = False
    has_reentry: bool = False
    has_zone_visit: bool = False
    has_billing: bool = False
    has_purchase: bool = False
    has_abandonment: bool = False
    billing_seen_at: list[datetime] = field(default_factory=list)
    transaction_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class PurchaseAttribution:
    transaction_id: str
    visitor_id: str
    basket_value: float
    timestamp: datetime
    attribution_confidence: float
    event_id: str


class EventRepository:
    def insert_event(self, event: EventIn) -> bool:
        metadata = event.metadata.model_dump(mode="json")
        try:
            with get_connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_id, store_id, camera_id, visitor_id, event_type, timestamp,
                        zone_id, dwell_ms, is_staff, confidence, queue_depth, sku_zone,
                        session_seq, raw_metadata_json, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.event_id),
                        event.store_id,
                        event.camera_id,
                        event.visitor_id,
                        event.event_type.value,
                        iso(event.timestamp),
                        event.zone_id,
                        event.dwell_ms,
                        int(event.is_staff),
                        event.confidence,
                        event.metadata.queue_depth,
                        event.metadata.sku_zone,
                        event.metadata.session_seq,
                        json.dumps(metadata, separators=(",", ":")),
                        iso(utc_now()),
                    ),
                )
                inserted = cursor.rowcount == 1
                if inserted:
                    self._persist_zone_visit(connection, event)
                    self._persist_queue_visit_from_event(
                        connection,
                        event_id=str(event.event_id),
                        store_id=event.store_id,
                        camera_id=event.camera_id,
                        visitor_id=event.visitor_id,
                        event_type=event.event_type.value,
                        timestamp=event.timestamp,
                        dwell_ms=event.dwell_ms,
                    )
                    self._derive_queue_event_from_cash_counter_zone(connection, event)
                return inserted
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(str(exc)) from exc

    def _persist_zone_visit(self, connection: sqlite3.Connection, event: EventIn) -> None:
        if event.zone_id is None:
            return

        timestamp = iso(event.timestamp)
        now = iso(utc_now())
        if event.event_type == EventType.ZONE_ENTER:
            existing = connection.execute(
                """
                SELECT visit_id FROM zone_visits
                WHERE store_id = ? AND visitor_id = ? AND zone_id = ? AND is_open = 1
                ORDER BY enter_time DESC
                LIMIT 1
                """,
                (event.store_id, event.visitor_id, event.zone_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO zone_visits (
                        visit_id, store_id, camera_id, visitor_id, zone_id,
                        enter_time, exit_time, dwell_ms, is_open, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0, 1, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        event.store_id,
                        event.camera_id,
                        event.visitor_id,
                        event.zone_id,
                        timestamp,
                        now,
                        now,
                    ),
                )
            return

        if event.event_type in {EventType.ZONE_EXIT, EventType.ZONE_DWELL}:
            open_visit = connection.execute(
                """
                SELECT visit_id, enter_time FROM zone_visits
                WHERE store_id = ? AND visitor_id = ? AND zone_id = ? AND is_open = 1
                ORDER BY enter_time DESC
                LIMIT 1
                """,
                (event.store_id, event.visitor_id, event.zone_id),
            ).fetchone()
            if open_visit is None:
                enter_time = iso(event.timestamp - timedelta(milliseconds=event.dwell_ms))
                visit_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO zone_visits (
                        visit_id, store_id, camera_id, visitor_id, zone_id,
                        enter_time, exit_time, dwell_ms, is_open, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0, 1, ?, ?)
                    """,
                    (
                        visit_id,
                        event.store_id,
                        event.camera_id,
                        event.visitor_id,
                        event.zone_id,
                        enter_time,
                        now,
                        now,
                    ),
                )
                open_visit = {"visit_id": visit_id, "enter_time": enter_time}

            dwell_ms = event.dwell_ms
            if dwell_ms == 0:
                elapsed = event.timestamp - parse_ts(open_visit["enter_time"])
                dwell_ms = max(
                    0,
                    int(elapsed.total_seconds() * 1000),
                )

            if event.event_type == EventType.ZONE_EXIT:
                connection.execute(
                    """
                    UPDATE zone_visits
                    SET exit_time = ?, dwell_ms = ?, is_open = 0, updated_at = ?
                    WHERE visit_id = ?
                    """,
                    (timestamp, dwell_ms, now, open_visit["visit_id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE zone_visits
                    SET dwell_ms = MAX(dwell_ms, ?), updated_at = ?
                    WHERE visit_id = ?
                    """,
                    (dwell_ms, now, open_visit["visit_id"]),
                )

    def _derive_queue_event_from_cash_counter_zone(
        self,
        connection: sqlite3.Connection,
        event: EventIn,
    ) -> None:
        if (event.zone_id or "").upper() != QUEUE_ZONE_ID:
            return
        if event.event_type == EventType.ZONE_ENTER:
            queue_event_type = EventType.QUEUE_JOIN.value
            dwell_ms = 0
        elif event.event_type == EventType.ZONE_EXIT:
            queue_event_type = EventType.QUEUE_EXIT.value
            dwell_ms = event.dwell_ms
        else:
            return

        queue_event_id = _derived_queue_event_id(str(event.event_id), queue_event_type)
        metadata = event.metadata.model_dump(mode="json")
        metadata.update(
            {
                "queue_zone": QUEUE_ZONE_ID,
                "source": "queue_analytics",
                "derived_from_event_id": str(event.event_id),
            }
        )
        now = iso(utc_now())
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO events (
                event_id, store_id, camera_id, visitor_id, event_type, timestamp,
                zone_id, dwell_ms, is_staff, confidence, queue_depth, sku_zone,
                session_seq, raw_metadata_json, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                queue_event_id,
                event.store_id,
                event.camera_id,
                event.visitor_id,
                queue_event_type,
                iso(event.timestamp),
                QUEUE_ZONE_ID,
                max(0, int(dwell_ms)),
                int(event.is_staff),
                event.confidence,
                QUEUE_ZONE_ID,
                event.metadata.session_seq,
                json.dumps(metadata, separators=(",", ":")),
                now,
            ),
        )
        if cursor.rowcount == 1:
            self._persist_queue_visit_from_event(
                connection,
                event_id=queue_event_id,
                store_id=event.store_id,
                camera_id=event.camera_id,
                visitor_id=event.visitor_id,
                event_type=queue_event_type,
                timestamp=event.timestamp,
                dwell_ms=dwell_ms,
            )

    def _persist_queue_visit_from_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        store_id: str,
        camera_id: str,
        visitor_id: str,
        event_type: str,
        timestamp: datetime,
        dwell_ms: int,
    ) -> None:
        if event_type not in {EventType.QUEUE_JOIN.value, EventType.QUEUE_EXIT.value}:
            return

        timestamp_iso = iso(timestamp)
        now = iso(utc_now())
        if event_type == EventType.QUEUE_JOIN.value:
            existing = connection.execute(
                """
                SELECT queue_visit_id FROM queue_visits
                WHERE store_id = ? AND visitor_id = ? AND is_open = 1
                ORDER BY join_time DESC
                LIMIT 1
                """,
                (store_id, visitor_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO queue_visits (
                        queue_visit_id, store_id, camera_id, visitor_id, join_event_id,
                        exit_event_id, join_time, exit_time, wait_time_ms,
                        is_open, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, 0, 1, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        store_id,
                        camera_id,
                        visitor_id,
                        event_id,
                        timestamp_iso,
                        now,
                        now,
                    ),
                )
            return

        open_visit = connection.execute(
            """
            SELECT queue_visit_id, join_time FROM queue_visits
            WHERE store_id = ? AND visitor_id = ? AND is_open = 1
            ORDER BY join_time DESC
            LIMIT 1
            """,
            (store_id, visitor_id),
        ).fetchone()
        if open_visit is None:
            join_time = iso(timestamp - timedelta(milliseconds=max(0, int(dwell_ms))))
            queue_visit_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO queue_visits (
                    queue_visit_id, store_id, camera_id, visitor_id, join_event_id,
                    exit_event_id, join_time, exit_time, wait_time_ms,
                    is_open, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    queue_visit_id,
                    store_id,
                    camera_id,
                    visitor_id,
                    event_id,
                    event_id,
                    join_time,
                    timestamp_iso,
                    max(0, int(dwell_ms)),
                    now,
                    now,
                ),
            )
            return

        wait_time_ms = max(0, int(dwell_ms))
        if wait_time_ms == 0:
            wait_time_ms = max(
                0,
                int((timestamp - parse_ts(open_visit["join_time"])).total_seconds() * 1000),
            )
        connection.execute(
            """
            UPDATE queue_visits
            SET exit_event_id = ?, exit_time = ?, wait_time_ms = ?, is_open = 0, updated_at = ?
            WHERE queue_visit_id = ?
            """,
            (event_id, timestamp_iso, wait_time_ms, now, open_visit["queue_visit_id"]),
        )

    def record_batch(self, accepted: int, duplicates: int, rejected: int) -> uuid.UUID:
        batch_id = uuid.uuid4()
        try:
            with get_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO ingest_batches (
                        batch_id, received_at, accepted_count, duplicate_count, rejected_count
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(batch_id), iso(utc_now()), accepted, duplicates, rejected),
                )
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(str(exc)) from exc
        return batch_id

    def events_for_store(
        self,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["store_id = ?"]
        params: list[Any] = [store_id]
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(iso(start))
        if end is not None:
            clauses.append("timestamp <= ?")
            params.append(iso(end))

        try:
            with get_connection() as connection:
                return list(
                    connection.execute(
                        f"""
                        SELECT * FROM events
                        WHERE {' AND '.join(clauses)}
                        ORDER BY timestamp ASC, session_seq ASC
                        """,
                        params,
                    )
                )
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(str(exc)) from exc

    def all_store_last_events(self) -> dict[str, datetime]:
        try:
            with get_connection() as connection:
                rows = connection.execute(
                    """
                    SELECT store_id, MAX(timestamp) AS last_event_timestamp
                    FROM events
                    GROUP BY store_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(str(exc)) from exc
        return {
            row["store_id"]: parse_ts(row["last_event_timestamp"])
            for row in rows
            if row["last_event_timestamp"] is not None
        }

    def pos_transactions_for_store(
        self,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["store_id = ?"]
        params: list[Any] = [store_id]
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(iso(start))
        if end is not None:
            clauses.append("timestamp <= ?")
            params.append(iso(end))

        try:
            with get_connection() as connection:
                return list(
                    connection.execute(
                        f"""
                        SELECT * FROM pos_transactions
                        WHERE {' AND '.join(clauses)}
                        ORDER BY timestamp ASC
                        """,
                        params,
                    )
                )
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(str(exc)) from exc

    def attribute_purchases_for_store(
        self,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        conversion_window: timedelta = timedelta(minutes=5),
    ) -> list[PurchaseAttribution]:
        try:
            with get_connection() as connection:
                event_rows = self._events_for_store(connection, store_id, start=start, end=end)
                sessions = build_sessions(event_rows)
                customer_sessions = [
                    session
                    for session in sessions.values()
                    if not session.is_staff and session.has_entry
                ]
                if not customer_sessions:
                    return []

                pos_rows = self._unattributed_pos_for_store(
                    connection,
                    store_id,
                    start=start,
                    end=end,
                )
                attributions: list[PurchaseAttribution] = []
                for tx in pos_rows:
                    match = _nearest_session_match(
                        tx_time=parse_ts(tx["timestamp"]),
                        sessions=customer_sessions,
                        conversion_window=conversion_window,
                    )
                    if match is None:
                        continue
                    session, distance = match
                    confidence = _attribution_confidence(distance, conversion_window)
                    event_id = _purchase_event_id(store_id, tx["transaction_id"])
                    attribution = PurchaseAttribution(
                        transaction_id=str(tx["transaction_id"]),
                        visitor_id=session.visitor_id,
                        basket_value=float(tx["basket_value"] or tx["basket_value_inr"]),
                        timestamp=parse_ts(tx["timestamp"]),
                        attribution_confidence=confidence,
                        event_id=event_id,
                    )
                    if self._persist_purchase_attribution(connection, store_id, attribution):
                        attributions.append(attribution)
                return attributions
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(str(exc)) from exc

    def _events_for_store(
        self,
        connection: sqlite3.Connection,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["store_id = ?"]
        params: list[Any] = [store_id]
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(iso(start))
        if end is not None:
            clauses.append("timestamp <= ?")
            params.append(iso(end))
        return list(
            connection.execute(
                f"""
                SELECT * FROM events
                WHERE {' AND '.join(clauses)}
                ORDER BY timestamp ASC, session_seq ASC
                """,
                params,
            )
        )

    def _unattributed_pos_for_store(
        self,
        connection: sqlite3.Connection,
        store_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[sqlite3.Row]:
        clauses = [
            "tx.store_id = ?",
            "tx.matched_visitor_id IS NULL",
            "pa.transaction_id IS NULL",
        ]
        params: list[Any] = [store_id]
        if start is not None:
            clauses.append("tx.timestamp >= ?")
            params.append(iso(start))
        if end is not None:
            clauses.append("tx.timestamp <= ?")
            params.append(iso(end))
        return list(
            connection.execute(
                f"""
                SELECT tx.*
                FROM pos_transactions tx
                LEFT JOIN purchase_attributions pa
                    ON pa.transaction_id = tx.transaction_id
                WHERE {' AND '.join(clauses)}
                ORDER BY tx.timestamp ASC, tx.transaction_id ASC
                """,
                params,
            )
        )

    def _persist_purchase_attribution(
        self,
        connection: sqlite3.Connection,
        store_id: str,
        attribution: PurchaseAttribution,
    ) -> bool:
        now = iso(utc_now())
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO purchase_attributions (
                transaction_id, store_id, visitor_id, basket_value, timestamp,
                attribution_confidence, event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attribution.transaction_id,
                store_id,
                attribution.visitor_id,
                attribution.basket_value,
                iso(attribution.timestamp),
                attribution.attribution_confidence,
                attribution.event_id,
                now,
            ),
        )
        if cursor.rowcount != 1:
            return False

        connection.execute(
            """
            UPDATE pos_transactions
            SET matched_visitor_id = ?
            WHERE transaction_id = ?
            """,
            (attribution.visitor_id, attribution.transaction_id),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO events (
                event_id, store_id, camera_id, visitor_id, event_type, timestamp,
                zone_id, dwell_ms, is_staff, confidence, queue_depth, sku_zone,
                session_seq, raw_metadata_json, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0, 0, ?, NULL, NULL, NULL, ?, ?)
            """,
            (
                attribution.event_id,
                store_id,
                "POS",
                attribution.visitor_id,
                EventType.PURCHASE.value,
                iso(attribution.timestamp),
                attribution.attribution_confidence,
                json.dumps(
                    {
                        "queue_depth": None,
                        "sku_zone": None,
                        "session_seq": None,
                        "transaction_id": attribution.transaction_id,
                        "basket_value": attribution.basket_value,
                        "attribution_confidence": attribution.attribution_confidence,
                        "source": "pos_attribution",
                    },
                    separators=(",", ":"),
                ),
                now,
            ),
        )
        return True


def _metadata(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["raw_metadata_json"])
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _is_billing_zone(zone_id: str | None, sku_zone: str | None) -> bool:
    billing_names = {
        "BILLING",
        "BILLING_COUNTER",
        "BILLING_QUEUE",
        "CASH_COUNTER",
        "CHECKOUT",
        "POS",
    }
    return (zone_id or "").upper() in billing_names or (sku_zone or "").upper() in billing_names


def build_sessions(
    rows: list[sqlite3.Row],
    *,
    pos_rows: list[sqlite3.Row] | None = None,
    conversion_window: timedelta = timedelta(minutes=5),
) -> dict[str, VisitorSession]:
    sessions: dict[str, VisitorSession] = {}
    for row in rows:
        visitor_id = row["visitor_id"]
        session = sessions.setdefault(
            visitor_id,
            VisitorSession(
                visitor_id=visitor_id,
                is_staff=bool(row["is_staff"]),
                first_seen=parse_ts(row["timestamp"]),
                last_seen=parse_ts(row["timestamp"]),
            ),
        )
        ts = parse_ts(row["timestamp"])
        session.first_seen = min(session.first_seen, ts)
        session.last_seen = max(session.last_seen, ts)
        session.is_staff = session.is_staff or bool(row["is_staff"])

        event_type = row["event_type"]
        metadata = _metadata(row)
        if event_type == EventType.ENTRY.value:
            session.has_entry = True
        elif event_type == EventType.REENTRY.value:
            session.has_reentry = True
            session.has_entry = True
        elif event_type == EventType.PURCHASE.value:
            session.has_purchase = True
        elif event_type in {
            EventType.ZONE_ENTER.value,
            EventType.ZONE_DWELL.value,
            EventType.ZONE_EXIT.value,
        }:
            session.has_zone_visit = True
        if event_type == EventType.BILLING_QUEUE_JOIN.value or _is_billing_zone(
            row["zone_id"], row["sku_zone"]
        ):
            session.has_billing = True
            session.billing_seen_at.append(ts)
        if event_type == EventType.BILLING_QUEUE_ABANDON.value:
            session.has_abandonment = True

        transaction_id = metadata.get("transaction_id")
        purchase_flag = metadata.get("purchase") is True or metadata.get("converted") is True
        if transaction_id or purchase_flag:
            session.has_purchase = True
            if transaction_id:
                session.transaction_ids.add(str(transaction_id))

    if pos_rows:
        _apply_pos_conversions(sessions, pos_rows, conversion_window)
    return sessions


def _apply_pos_conversions(
    sessions: dict[str, VisitorSession],
    pos_rows: list[sqlite3.Row],
    conversion_window: timedelta,
) -> None:
    customer_sessions = [
        session for session in sessions.values() if not session.is_staff and session.has_entry
    ]
    used_transactions: set[str] = set()
    for tx in pos_rows:
        transaction_id = str(tx["transaction_id"])
        if transaction_id in used_transactions:
            continue
        tx_time = parse_ts(tx["timestamp"])
        available_sessions = [session for session in customer_sessions if not session.has_purchase]
        match = _nearest_session_match(
            tx_time=tx_time,
            sessions=available_sessions,
            conversion_window=conversion_window,
        )
        if match is None:
            continue
        matched_session, _distance = match
        matched_session.has_purchase = True
        matched_session.transaction_ids.add(transaction_id)
        used_transactions.add(transaction_id)


def _nearest_session_match(
    *,
    tx_time: datetime,
    sessions: list[VisitorSession],
    conversion_window: timedelta,
) -> tuple[VisitorSession, timedelta] | None:
    candidates: list[tuple[timedelta, datetime, VisitorSession]] = []
    for session in sessions:
        distance = _session_distance(tx_time, session)
        if distance <= conversion_window:
            candidates.append((distance, session.last_seen, session))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2].visitor_id))
    distance, _, session = candidates[0]
    return session, distance


def _session_distance(tx_time: datetime, session: VisitorSession) -> timedelta:
    if session.first_seen <= tx_time <= session.last_seen:
        return timedelta(0)
    if tx_time < session.first_seen:
        return session.first_seen - tx_time
    return tx_time - session.last_seen


def _attribution_confidence(distance: timedelta, conversion_window: timedelta) -> float:
    window_seconds = max(conversion_window.total_seconds(), 1.0)
    confidence = 1.0 - (distance.total_seconds() / window_seconds)
    return round(max(0.0, min(1.0, confidence)), 4)


def _purchase_event_id(store_id: str, transaction_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"purchase:{store_id}:{transaction_id}"))


def _derived_queue_event_id(source_event_id: str, queue_event_type: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"queue:{source_event_id}:{queue_event_type}"))


def queue_analytics(rows: list[sqlite3.Row]) -> dict[str, Any]:
    open_visitors: dict[str, datetime] = {}
    wait_times: list[int] = []
    max_depth = 0

    for row in rows:
        if row["is_staff"]:
            continue
        event_type = row["event_type"]
        visitor_id = row["visitor_id"]
        timestamp = parse_ts(row["timestamp"])
        if event_type == EventType.QUEUE_JOIN.value:
            if visitor_id not in open_visitors:
                open_visitors[visitor_id] = timestamp
            max_depth = max(max_depth, len(open_visitors))
        elif event_type == EventType.QUEUE_EXIT.value:
            joined_at = open_visitors.pop(visitor_id, None)
            wait_time_ms = int(row["dwell_ms"] or 0)
            if wait_time_ms == 0 and joined_at is not None:
                wait_time_ms = max(0, int((timestamp - joined_at).total_seconds() * 1000))
            wait_times.append(max(0, wait_time_ms))

    return {
        "current_depth": len(open_visitors),
        "max_depth": max_depth,
        "avg_wait_time": round(sum(wait_times) / len(wait_times), 2) if wait_times else 0.0,
        "peak_wait_time": max(wait_times, default=0),
    }


def zone_dwell(rows: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    zones: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "dwell": 0,
            "dwell_values": [],
            "visitors": set(),
            "visits": 0,
            "visits_by_visitor": defaultdict(int),
        }
    )
    open_visits: dict[tuple[str, str], dict[str, Any]] = {}

    def close_visit(visitor_id: str, zone_id: str, dwell_ms: int) -> None:
        zone = zones[zone_id]
        safe_dwell_ms = max(0, int(dwell_ms))
        zone["dwell"] += safe_dwell_ms
        zone["dwell_values"].append(safe_dwell_ms)
        zone["visitors"].add(visitor_id)
        zone["visits"] += 1
        zone["visits_by_visitor"][visitor_id] += 1

    for row in rows:
        if row["is_staff"]:
            continue
        zone_id = row["zone_id"]
        if not zone_id:
            continue
        visitor_id = row["visitor_id"]
        visit_key = (visitor_id, zone_id)
        event_type = row["event_type"]
        if event_type == EventType.ZONE_ENTER.value:
            if visit_key in open_visits:
                close_visit(visitor_id, zone_id, open_visits[visit_key]["dwell_ms"])
            open_visits[visit_key] = {"dwell_ms": 0}
        elif event_type == EventType.ZONE_DWELL.value:
            if visit_key not in open_visits:
                open_visits[visit_key] = {"dwell_ms": 0}
            open_visits[visit_key]["dwell_ms"] = max(
                int(open_visits[visit_key]["dwell_ms"]),
                int(row["dwell_ms"]),
            )
        elif event_type == EventType.ZONE_EXIT.value:
            dwell_ms = int(row["dwell_ms"])
            if visit_key in open_visits:
                dwell_ms = max(dwell_ms, int(open_visits[visit_key]["dwell_ms"]))
                del open_visits[visit_key]
            close_visit(visitor_id, zone_id, dwell_ms)

    for (visitor_id, zone_id), visit in open_visits.items():
        close_visit(visitor_id, zone_id, int(visit["dwell_ms"]))

    return zones
