from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_connection, initialize_database  # noqa: E402
from app.repository import iso  # noqa: E402

LOGGER = logging.getLogger("pos_loader")

REQUIRED_COLUMNS = {
    "invoice_number",
    "order_date",
    "order_time",
    "store_id",
    "sku",
    "product_name",
    "brand_name",
    "salesperson_name",
    "total_amount",
}

DEFAULT_STORE_MAP = {"ST1008": "STORE_BLR_002"}


@dataclass(frozen=True)
class PosTransaction:
    transaction_id: str
    store_id: str
    timestamp: datetime
    basket_value: float
    product: str
    brand: str
    salesperson: str


@dataclass(frozen=True)
class RowError:
    row_number: int
    code: str
    message: str


@dataclass
class LoadReport:
    file: str
    total_rows: int = 0
    valid_rows: int = 0
    inserted: int = 0
    duplicates_in_file: int = 0
    duplicates_in_database: int = 0
    rejected: int = 0
    batch_size: int = 500
    errors: list[RowError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["errors"] = [asdict(error) for error in self.errors]
        return payload


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def normalize_timestamp(order_date: str, order_time: str) -> datetime:
    raw = f"{order_date.strip()} {order_time.strip()}"
    for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(f"unsupported timestamp format: {raw!r}")


def validate_headers(headers: list[str] | None) -> None:
    if headers is None:
        raise ValueError("CSV file is empty")
    missing = sorted(REQUIRED_COLUMNS - set(headers))
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")


def parse_row(
    row: dict[str, str],
    *,
    row_number: int,
    store_map: dict[str, str],
    allowed_stores: set[str],
) -> PosTransaction:
    source_store_id = _required(row, "store_id", row_number)
    store_id = store_map.get(source_store_id, source_store_id)
    if store_id not in allowed_stores:
        raise ValueError(f"row {row_number}: unknown store_id {source_store_id!r}")

    invoice_number = _required(row, "invoice_number", row_number)
    sku = _required(row, "sku", row_number)
    transaction_id = f"{invoice_number}:{sku}"
    timestamp = normalize_timestamp(
        _required(row, "order_date", row_number),
        _required(row, "order_time", row_number),
    )
    basket_value = _parse_amount(_required(row, "total_amount", row_number), row_number)

    return PosTransaction(
        transaction_id=transaction_id,
        store_id=store_id,
        timestamp=timestamp,
        basket_value=basket_value,
        product=_required(row, "product_name", row_number),
        brand=_required(row, "brand_name", row_number),
        salesperson=(row.get("salesperson_name") or "").strip(),
    )


def _required(row: dict[str, str], column: str, row_number: int) -> str:
    value = (row.get(column) or "").strip()
    if not value:
        raise ValueError(f"row {row_number}: missing {column}")
    return value


def _parse_amount(value: str, row_number: int) -> float:
    try:
        amount = float(value)
    except ValueError as exc:
        raise ValueError(f"row {row_number}: invalid total_amount {value!r}") from exc
    if amount < 0:
        raise ValueError(f"row {row_number}: total_amount must be non-negative")
    return amount


def load_pos_csv(
    path: str | Path,
    *,
    batch_size: int = 500,
    allowed_stores: set[str] | None = None,
    store_map: dict[str, str] | None = None,
) -> LoadReport:
    csv_path = Path(path)
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if not csv_path.exists():
        raise FileNotFoundError(f"POS CSV does not exist: {csv_path}")

    allowed_stores = allowed_stores or {"STORE_BLR_002"}
    store_map = store_map or DEFAULT_STORE_MAP
    report = LoadReport(file=str(csv_path), batch_size=batch_size)
    seen_transaction_ids: set[str] = set()
    pending: list[PosTransaction] = []

    initialize_database()
    LOGGER.info("pos_load_started", extra={"file": str(csv_path), "batch_size": batch_size})

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_headers(reader.fieldnames)
        with get_connection() as connection:
            for row_number, row in enumerate(reader, start=2):
                report.total_rows += 1
                try:
                    transaction = parse_row(
                        row,
                        row_number=row_number,
                        store_map=store_map,
                        allowed_stores=allowed_stores,
                    )
                except ValueError as exc:
                    report.rejected += 1
                    report.errors.append(
                        RowError(row_number=row_number, code="VALIDATION_ERROR", message=str(exc))
                    )
                    continue

                if transaction.transaction_id in seen_transaction_ids:
                    report.duplicates_in_file += 1
                    report.errors.append(
                        RowError(
                            row_number=row_number,
                            code="DUPLICATE_IN_FILE",
                            message=f"duplicate transaction_id {transaction.transaction_id}",
                        )
                    )
                    continue

                seen_transaction_ids.add(transaction.transaction_id)
                report.valid_rows += 1
                pending.append(transaction)
                if len(pending) >= batch_size:
                    _insert_batch(connection, pending, report)
                    pending.clear()

            if pending:
                _insert_batch(connection, pending, report)

    LOGGER.info(
        "pos_load_finished",
        extra={
            "file": str(csv_path),
            "total_rows": report.total_rows,
            "inserted": report.inserted,
            "rejected": report.rejected,
            "duplicates_in_file": report.duplicates_in_file,
            "duplicates_in_database": report.duplicates_in_database,
        },
    )
    return report


def _insert_batch(
    connection: sqlite3.Connection,
    transactions: list[PosTransaction],
    report: LoadReport,
) -> None:
    for transaction in transactions:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO pos_transactions (
                transaction_id, store_id, timestamp, basket_value_inr, basket_value,
                product, brand, salesperson, matched_visitor_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                transaction.transaction_id,
                transaction.store_id,
                iso(transaction.timestamp),
                transaction.basket_value,
                transaction.basket_value,
                transaction.product,
                transaction.brand,
                transaction.salesperson,
            ),
        )
        if cursor.rowcount == 1:
            report.inserted += 1
        else:
            report.duplicates_in_database += 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and load POS transaction CSV data.")
    parser.add_argument("--file", required=True, help="Path to data/pos_transactions.csv")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--store-id", default="STORE_BLR_002", help="Allowed canonical store id")
    parser.add_argument("--source-store-id", default="ST1008", help="Store id used by the POS CSV")
    parser.add_argument("--report", default="data/pos_load_report.json")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    configure_logging(args.log_level)
    report = load_pos_csv(
        args.file,
        batch_size=args.batch_size,
        allowed_stores={args.store_id},
        store_map={args.source_store_id: args.store_id},
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_payload = report.to_dict()
    report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    print(json.dumps(report_payload, indent=2))
    return 1 if report.rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
