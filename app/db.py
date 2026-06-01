from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    zone_id TEXT NULL,
    dwell_ms INTEGER NOT NULL DEFAULT 0,
    is_staff INTEGER NOT NULL,
    confidence REAL NOT NULL,
    queue_depth INTEGER NULL,
    sku_zone TEXT NULL,
    session_seq INTEGER NULL,
    raw_metadata_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_batches (
    batch_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    accepted_count INTEGER NOT NULL,
    duplicate_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pos_transactions (
    transaction_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    basket_value_inr REAL NOT NULL,
    basket_value REAL NULL,
    product TEXT NULL,
    brand TEXT NULL,
    salesperson TEXT NULL,
    matched_visitor_id TEXT NULL
);

CREATE TABLE IF NOT EXISTS purchase_attributions (
    transaction_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    basket_value REAL NOT NULL,
    timestamp TEXT NOT NULL,
    attribution_confidence REAL NOT NULL,
    event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(transaction_id) REFERENCES pos_transactions(transaction_id)
);

CREATE TABLE IF NOT EXISTS queue_visits (
    queue_visit_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    join_event_id TEXT NOT NULL,
    exit_event_id TEXT NULL,
    join_time TEXT NOT NULL,
    exit_time TEXT NULL,
    wait_time_ms INTEGER NOT NULL DEFAULT 0,
    is_open INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zone_visits (
    visit_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    zone_id TEXT NOT NULL,
    enter_time TEXT NOT NULL,
    exit_time TEXT NULL,
    dwell_ms INTEGER NOT NULL DEFAULT 0,
    is_open INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_store_time ON events(store_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_visitor ON events(visitor_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_zone ON events(store_id, zone_id);
CREATE INDEX IF NOT EXISTS idx_pos_store_time ON pos_transactions(store_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_purchase_attr_store_time
ON purchase_attributions(store_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_queue_visits_store_time
ON queue_visits(store_id, join_time);
CREATE INDEX IF NOT EXISTS idx_queue_visits_open
ON queue_visits(store_id, visitor_id, is_open, join_time);
CREATE INDEX IF NOT EXISTS idx_zone_visits_lookup
ON zone_visits(store_id, visitor_id, zone_id, is_open, enter_time);
CREATE INDEX IF NOT EXISTS idx_zone_visits_store_zone
ON zone_visits(store_id, zone_id, enter_time);
"""


def connect() -> sqlite3.Connection:
    settings = get_settings()
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    with connect() as connection:
        connection.executescript(SCHEMA_SQL)
        _ensure_pos_transaction_columns(connection)


def _ensure_pos_transaction_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(pos_transactions)").fetchall()
    }
    columns = {
        "basket_value": "REAL NULL",
        "product": "TEXT NULL",
        "brand": "TEXT NULL",
        "salesperson": "TEXT NULL",
    }
    for name, definition in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE pos_transactions ADD COLUMN {name} {definition}")


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
