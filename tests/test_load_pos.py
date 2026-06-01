from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from app.config import get_settings
from scripts.load_pos import load_pos_csv

HEADERS = [
    "invoice_number",
    "order_date",
    "order_time",
    "store_id",
    "sku",
    "product_name",
    "brand_name",
    "salesperson_name",
    "total_amount",
]


def write_pos_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def pos_row(**overrides: str) -> dict[str, str]:
    row = {
        "invoice_number": "INV001",
        "order_date": "10-04-2026",
        "order_time": "16:55:36",
        "store_id": "ST1008",
        "sku": "SKU001",
        "product_name": "DERMDOC Body Wash",
        "brand_name": "DERMDOC",
        "salesperson_name": "kasthuri v",
        "total_amount": "274.36",
    }
    row.update(overrides)
    return row


def test_load_pos_persists_valid_rows_with_normalized_timestamp(client, tmp_path):
    csv_path = tmp_path / "pos.csv"
    write_pos_csv(csv_path, [pos_row()])

    report = load_pos_csv(csv_path, batch_size=1)

    assert report.inserted == 1
    assert report.rejected == 0
    connection = sqlite3.connect(get_settings().database_path)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT transaction_id, store_id, timestamp, basket_value, basket_value_inr,
               product, brand, salesperson
        FROM pos_transactions
        """
    ).fetchone()
    connection.close()

    assert row["transaction_id"] == "INV001:SKU001"
    assert row["store_id"] == "STORE_BLR_002"
    assert row["timestamp"] == "2026-04-10T16:55:36Z"
    assert row["basket_value"] == 274.36
    assert row["basket_value_inr"] == 274.36
    assert row["product"] == "DERMDOC Body Wash"
    assert row["brand"] == "DERMDOC"
    assert row["salesperson"] == "kasthuri v"


def test_load_pos_reports_file_and_database_duplicates(client, tmp_path):
    csv_path = tmp_path / "pos.csv"
    write_pos_csv(
        csv_path,
        [
            pos_row(),
            pos_row(),
            pos_row(invoice_number="INV002", sku="SKU002", total_amount="99"),
        ],
    )

    first = load_pos_csv(csv_path, batch_size=2)
    second = load_pos_csv(csv_path, batch_size=2)

    assert first.inserted == 2
    assert first.duplicates_in_file == 1
    assert first.duplicates_in_database == 0
    assert first.errors[0].code == "DUPLICATE_IN_FILE"
    assert second.inserted == 0
    assert second.duplicates_in_file == 1
    assert second.duplicates_in_database == 2


def test_load_pos_reports_validation_errors_without_aborting_batch(client, tmp_path):
    csv_path = tmp_path / "pos.csv"
    write_pos_csv(
        csv_path,
        [
            pos_row(store_id="UNKNOWN"),
            pos_row(invoice_number="INV002", sku="SKU002", total_amount="not-money"),
            pos_row(invoice_number="INV003", sku="SKU003"),
        ],
    )

    report = load_pos_csv(csv_path, batch_size=2)

    assert report.total_rows == 3
    assert report.inserted == 1
    assert report.rejected == 2
    assert [error.code for error in report.errors] == [
        "VALIDATION_ERROR",
        "VALIDATION_ERROR",
    ]
