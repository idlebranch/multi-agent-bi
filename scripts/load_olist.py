"""Stream the original Olist CSV bundle into an indexed SQLite warehouse."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "raw" / "olist.zip"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "olist.sqlite"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "active_dataset.json"
SEMANTIC_MODEL = PROJECT_ROOT / "data" / "olist_semantic_model.json"
BATCH_SIZE = 5_000

FILES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "translations": "product_category_name_translation.csv",
}


def text_value(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def int_value(value: str | None) -> int | None:
    value = text_value(value)
    return int(value) if value is not None else None


def float_value(value: str | None) -> float | None:
    value = text_value(value)
    return float(value) if value is not None else None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_members(bundle: zipfile.ZipFile) -> dict[str, str]:
    by_basename: dict[str, list[str]] = defaultdict(list)
    for member in bundle.namelist():
        if not member.endswith("/"):
            by_basename[Path(member).name].append(member)

    resolved: dict[str, str] = {}
    for key, expected in FILES.items():
        matches = by_basename.get(expected, [])
        if len(matches) != 1:
            raise RuntimeError(
                f"expected exactly one {expected!r} in archive, found {len(matches)}"
            )
        resolved[key] = matches[0]
    return resolved


def csv_rows(bundle: zipfile.ZipFile, member: str) -> Iterator[dict[str, str]]:
    with bundle.open(member, "r") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            yield from csv.DictReader(text)


def batched(rows: Iterable[Sequence[Any]], size: int = BATCH_SIZE) -> Iterator[list[Sequence[Any]]]:
    batch: list[Sequence[Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def insert_rows(
    conn: sqlite3.Connection,
    sql: str,
    rows: Iterable[Sequence[Any]],
    *,
    label: str,
) -> int:
    count = 0
    for batch in batched(rows):
        conn.executemany(sql, batch)
        count += len(batch)
        if count % 100_000 == 0:
            print(f"  {label}: {count:,} rows", flush=True)
    print(f"  {label}: {count:,} rows", flush=True)
    return count


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE geolocation (
            zip_code_prefix INTEGER PRIMARY KEY,
            latitude REAL,
            longitude REAL,
            city TEXT,
            state TEXT,
            source_row_count INTEGER NOT NULL CHECK (source_row_count > 0)
        );

        CREATE TABLE category_translations (
            category_name TEXT PRIMARY KEY,
            category_name_english TEXT NOT NULL
        );

        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            customer_unique_id TEXT NOT NULL,
            zip_code_prefix INTEGER,
            city TEXT,
            state TEXT
        );

        CREATE TABLE sellers (
            seller_id TEXT PRIMARY KEY,
            zip_code_prefix INTEGER,
            city TEXT,
            state TEXT
        );

        CREATE TABLE products (
            product_id TEXT PRIMARY KEY,
            category_name TEXT,
            name_length INTEGER,
            description_length INTEGER,
            photos_qty INTEGER,
            weight_g REAL,
            length_cm REAL,
            height_cm REAL,
            width_cm REAL
        );

        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers(customer_id),
            status TEXT NOT NULL,
            purchase_timestamp TEXT NOT NULL,
            approved_at TEXT,
            delivered_carrier_timestamp TEXT,
            delivered_customer_timestamp TEXT,
            estimated_delivery_timestamp TEXT
        );

        CREATE TABLE order_items (
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            order_item_id INTEGER NOT NULL,
            product_id TEXT NOT NULL REFERENCES products(product_id),
            seller_id TEXT NOT NULL REFERENCES sellers(seller_id),
            shipping_limit_timestamp TEXT,
            price REAL NOT NULL CHECK (price >= 0),
            freight_value REAL NOT NULL CHECK (freight_value >= 0),
            PRIMARY KEY (order_id, order_item_id)
        );

        CREATE TABLE payments (
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            payment_sequential INTEGER NOT NULL,
            payment_type TEXT,
            payment_installments INTEGER,
            payment_value REAL CHECK (payment_value >= 0),
            PRIMARY KEY (order_id, payment_sequential)
        );

        CREATE TABLE reviews (
            review_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            review_score INTEGER NOT NULL CHECK (review_score BETWEEN 1 AND 5),
            comment_title TEXT,
            comment_message TEXT,
            creation_timestamp TEXT,
            answer_timestamp TEXT,
            PRIMARY KEY (review_id, order_id)
        );
        """
    )


def load_geolocation(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, str]],
) -> tuple[int, int]:
    coordinates: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    locations: dict[int, Counter[tuple[str | None, str | None]]] = defaultdict(Counter)
    raw_count = 0

    for row in rows:
        prefix = int_value(row.get("geolocation_zip_code_prefix"))
        latitude = float_value(row.get("geolocation_lat"))
        longitude = float_value(row.get("geolocation_lng"))
        if prefix is None or latitude is None or longitude is None:
            continue
        aggregate = coordinates[prefix]
        aggregate[0] += latitude
        aggregate[1] += longitude
        aggregate[2] += 1
        locations[prefix][
            (text_value(row.get("geolocation_city")), text_value(row.get("geolocation_state")))
        ] += 1
        raw_count += 1
        if raw_count % 250_000 == 0:
            print(f"  geolocation source: {raw_count:,} rows", flush=True)

    def aggregated_rows() -> Iterator[Sequence[Any]]:
        for prefix in sorted(coordinates):
            latitude_sum, longitude_sum, count = coordinates[prefix]
            city, state = locations[prefix].most_common(1)[0][0]
            yield (prefix, latitude_sum / count, longitude_sum / count, city, state, int(count))

    aggregate_count = insert_rows(
        conn,
        "INSERT INTO geolocation VALUES (?, ?, ?, ?, ?, ?)",
        aggregated_rows(),
        label="geolocation aggregate",
    )
    return raw_count, aggregate_count


def create_indexes_and_semantic_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_customers_unique_id ON customers(customer_unique_id);
        CREATE INDEX idx_customers_state ON customers(state);
        CREATE INDEX idx_orders_customer_purchase ON orders(customer_id, purchase_timestamp);
        CREATE INDEX idx_orders_purchase_status ON orders(purchase_timestamp, status);
        CREATE INDEX idx_order_items_product ON order_items(product_id);
        CREATE INDEX idx_order_items_seller ON order_items(seller_id);
        CREATE INDEX idx_payments_type ON payments(payment_type);
        CREATE INDEX idx_reviews_order ON reviews(order_id);
        CREATE INDEX idx_reviews_score ON reviews(review_score);
        CREATE INDEX idx_products_category ON products(category_name);
        CREATE INDEX idx_sellers_state ON sellers(state);

        CREATE TABLE order_financials AS
        WITH item_totals AS (
            SELECT order_id,
                   COUNT(*) AS item_count,
                   ROUND(SUM(price), 2) AS item_value,
                   ROUND(SUM(freight_value), 2) AS freight_value
            FROM order_items
            GROUP BY order_id
        ),
        payment_totals AS (
            SELECT order_id,
                   COUNT(*) AS payment_count,
                   ROUND(SUM(payment_value), 2) AS payment_value
            FROM payments
            GROUP BY order_id
        )
        SELECT o.order_id,
               o.customer_id,
               c.customer_unique_id,
               c.state AS customer_state,
               o.status,
               o.purchase_timestamp,
               COALESCE(i.item_count, 0) AS item_count,
               COALESCE(i.item_value, 0) AS item_value,
               COALESCE(i.freight_value, 0) AS freight_value,
               COALESCE(p.payment_count, 0) AS payment_count,
               COALESCE(p.payment_value, 0) AS payment_value
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        LEFT JOIN item_totals i ON i.order_id = o.order_id
        LEFT JOIN payment_totals p ON p.order_id = o.order_id;

        CREATE TABLE order_delivery_metrics AS
        SELECT o.order_id,
               o.customer_id,
               c.customer_unique_id,
               c.state AS customer_state,
               o.purchase_timestamp,
               o.delivered_customer_timestamp,
               o.estimated_delivery_timestamp,
               ROUND(julianday(o.delivered_customer_timestamp) -
                     julianday(o.purchase_timestamp), 2) AS delivery_days,
               ROUND(julianday(o.delivered_customer_timestamp) -
                     julianday(o.estimated_delivery_timestamp), 2) AS days_vs_estimate,
               CASE
                   WHEN o.delivered_customer_timestamp IS NULL OR
                        o.estimated_delivery_timestamp IS NULL THEN NULL
                   WHEN o.delivered_customer_timestamp <= o.estimated_delivery_timestamp THEN 1
                   ELSE 0
               END AS delivered_on_time
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        WHERE o.status = 'delivered';

        CREATE TABLE product_sales AS
        SELECT oi.order_id,
               oi.order_item_id,
               o.purchase_timestamp,
               o.status AS order_status,
               c.state AS customer_state,
               oi.product_id,
               COALESCE(t.category_name_english, p.category_name, 'unknown') AS category_name,
               oi.seller_id,
               s.state AS seller_state,
               oi.price,
               oi.freight_value
        FROM order_items oi
        JOIN orders o ON o.order_id = oi.order_id
        JOIN customers c ON c.customer_id = o.customer_id
        JOIN products p ON p.product_id = oi.product_id
        JOIN sellers s ON s.seller_id = oi.seller_id
        LEFT JOIN category_translations t ON t.category_name = p.category_name;

        CREATE TABLE category_sales_summary AS
        SELECT category_name,
               COUNT(*) AS item_count,
               COUNT(DISTINCT order_id) AS order_count,
               ROUND(SUM(price), 2) AS delivered_gmv,
               ROUND(SUM(freight_value), 2) AS freight_value
        FROM product_sales
        WHERE order_status = 'delivered'
        GROUP BY category_name;

        CREATE TABLE delivery_kpis AS
        SELECT COUNT(*) AS delivered_orders,
               COUNT(delivered_on_time) AS measured_delivery_orders,
               SUM(delivered_on_time) AS on_time_orders,
               ROUND(AVG(delivered_on_time) * 100, 4) AS on_time_delivery_pct,
               ROUND(AVG(delivery_days), 2) AS average_delivery_days
        FROM order_delivery_metrics;

        CREATE TABLE payment_type_summary AS
        SELECT COALESCE(payment_type, 'unknown') AS payment_type,
               COUNT(*) AS payment_count,
               COUNT(DISTINCT order_id) AS order_count,
               ROUND(SUM(payment_value), 2) AS payment_value
        FROM payments
        GROUP BY COALESCE(payment_type, 'unknown');

        CREATE TABLE customer_order_summary AS
        SELECT customer_unique_id,
               COUNT(*) AS order_count,
               SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered_order_count,
               ROUND(SUM(item_value), 2) AS all_order_item_value,
               ROUND(SUM(CASE WHEN status = 'delivered' THEN item_value ELSE 0 END), 2)
                   AS delivered_gmv
        FROM order_financials
        GROUP BY customer_unique_id;

        CREATE UNIQUE INDEX idx_order_financials_order ON order_financials(order_id);
        CREATE INDEX idx_order_financials_status ON order_financials(status);
        CREATE INDEX idx_order_financials_purchase ON order_financials(purchase_timestamp);
        CREATE INDEX idx_order_financials_customer ON order_financials(customer_unique_id);

        CREATE UNIQUE INDEX idx_delivery_metrics_order ON order_delivery_metrics(order_id);
        CREATE INDEX idx_delivery_metrics_state_time
            ON order_delivery_metrics(customer_state, delivered_on_time);
        CREATE INDEX idx_delivery_metrics_purchase
            ON order_delivery_metrics(purchase_timestamp);

        CREATE UNIQUE INDEX idx_product_sales_item
            ON product_sales(order_id, order_item_id);
        CREATE INDEX idx_product_sales_status_purchase
            ON product_sales(order_status, purchase_timestamp);
        CREATE INDEX idx_product_sales_status_category
            ON product_sales(order_status, category_name);
        CREATE INDEX idx_product_sales_status_seller_state
            ON product_sales(order_status, seller_state);
        CREATE INDEX idx_product_sales_status_product
            ON product_sales(order_status, product_id);
        CREATE INDEX idx_product_sales_state_pair
            ON product_sales(order_status, customer_state, seller_state);

        CREATE UNIQUE INDEX idx_category_summary_name
            ON category_sales_summary(category_name);
        CREATE INDEX idx_category_summary_gmv
            ON category_sales_summary(delivered_gmv DESC);
        CREATE UNIQUE INDEX idx_payment_summary_type
            ON payment_type_summary(payment_type);
        CREATE UNIQUE INDEX idx_customer_summary_customer
            ON customer_order_summary(customer_unique_id);
        CREATE INDEX idx_customer_summary_orders
            ON customer_order_summary(order_count);
        CREATE INDEX idx_customer_summary_delivered
            ON customer_order_summary(delivered_order_count, delivered_gmv);

        ANALYZE;
        """
    )


def build_database(source: Path, output: Path, *, replace: bool) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Olist archive not found: {source}")
    if output.exists() and not replace:
        raise FileExistsError(f"database already exists (pass --replace): {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    building = output.with_name(output.name + ".building")
    if building.exists():
        building.unlink()

    counts: dict[str, int] = {}
    started = datetime.now(timezone.utc)

    try:
        with zipfile.ZipFile(source) as bundle:
            members = find_members(bundle)
            print(f"Source archive verified: {len(members)} CSV files", flush=True)

            conn = sqlite3.connect(building)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = OFF")
                conn.execute("PRAGMA synchronous = OFF")
                conn.execute("PRAGMA temp_store = MEMORY")
                create_schema(conn)

                counts["category_translations"] = insert_rows(
                    conn,
                    "INSERT INTO category_translations VALUES (?, ?)",
                    (
                        (
                            text_value(row.get("product_category_name")),
                            text_value(row.get("product_category_name_english")),
                        )
                        for row in csv_rows(bundle, members["translations"])
                    ),
                    label="category_translations",
                )
                counts["geolocation_source"], counts["geolocation"] = load_geolocation(
                    conn, csv_rows(bundle, members["geolocation"])
                )
                counts["customers"] = insert_rows(
                    conn,
                    "INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
                    (
                        (
                            text_value(row.get("customer_id")),
                            text_value(row.get("customer_unique_id")),
                            int_value(row.get("customer_zip_code_prefix")),
                            text_value(row.get("customer_city")),
                            text_value(row.get("customer_state")),
                        )
                        for row in csv_rows(bundle, members["customers"])
                    ),
                    label="customers",
                )
                counts["sellers"] = insert_rows(
                    conn,
                    "INSERT INTO sellers VALUES (?, ?, ?, ?)",
                    (
                        (
                            text_value(row.get("seller_id")),
                            int_value(row.get("seller_zip_code_prefix")),
                            text_value(row.get("seller_city")),
                            text_value(row.get("seller_state")),
                        )
                        for row in csv_rows(bundle, members["sellers"])
                    ),
                    label="sellers",
                )
                counts["products"] = insert_rows(
                    conn,
                    "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            text_value(row.get("product_id")),
                            text_value(row.get("product_category_name")),
                            int_value(row.get("product_name_lenght")),
                            int_value(row.get("product_description_lenght")),
                            int_value(row.get("product_photos_qty")),
                            float_value(row.get("product_weight_g")),
                            float_value(row.get("product_length_cm")),
                            float_value(row.get("product_height_cm")),
                            float_value(row.get("product_width_cm")),
                        )
                        for row in csv_rows(bundle, members["products"])
                    ),
                    label="products",
                )
                counts["orders"] = insert_rows(
                    conn,
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            text_value(row.get("order_id")),
                            text_value(row.get("customer_id")),
                            text_value(row.get("order_status")),
                            text_value(row.get("order_purchase_timestamp")),
                            text_value(row.get("order_approved_at")),
                            text_value(row.get("order_delivered_carrier_date")),
                            text_value(row.get("order_delivered_customer_date")),
                            text_value(row.get("order_estimated_delivery_date")),
                        )
                        for row in csv_rows(bundle, members["orders"])
                    ),
                    label="orders",
                )
                counts["order_items"] = insert_rows(
                    conn,
                    "INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            text_value(row.get("order_id")),
                            int_value(row.get("order_item_id")),
                            text_value(row.get("product_id")),
                            text_value(row.get("seller_id")),
                            text_value(row.get("shipping_limit_date")),
                            float_value(row.get("price")),
                            float_value(row.get("freight_value")),
                        )
                        for row in csv_rows(bundle, members["order_items"])
                    ),
                    label="order_items",
                )
                counts["payments"] = insert_rows(
                    conn,
                    "INSERT INTO payments VALUES (?, ?, ?, ?, ?)",
                    (
                        (
                            text_value(row.get("order_id")),
                            int_value(row.get("payment_sequential")),
                            text_value(row.get("payment_type")),
                            int_value(row.get("payment_installments")),
                            float_value(row.get("payment_value")),
                        )
                        for row in csv_rows(bundle, members["payments"])
                    ),
                    label="payments",
                )
                counts["reviews"] = insert_rows(
                    conn,
                    "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            text_value(row.get("review_id")),
                            text_value(row.get("order_id")),
                            int_value(row.get("review_score")),
                            text_value(row.get("review_comment_title")),
                            text_value(row.get("review_comment_message")),
                            text_value(row.get("review_creation_date")),
                            text_value(row.get("review_answer_timestamp")),
                        )
                        for row in csv_rows(bundle, members["reviews"])
                    ),
                    label="reviews",
                )

                create_indexes_and_semantic_tables(conn)
                foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
                if foreign_key_errors:
                    raise RuntimeError(
                        f"foreign-key audit failed with {len(foreign_key_errors)} violations"
                    )
                as_of_date = str(
                    conn.execute("SELECT date(MAX(purchase_timestamp)) FROM orders").fetchone()[0]
                )
                conn.commit()
            finally:
                conn.close()

        os.replace(building, output)
    except Exception:
        if building.exists():
            building.unlink()
        raise

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return {
        "name": "olist_brazilian_ecommerce",
        "database": output.name,
        "semantic_model": SEMANTIC_MODEL.name,
        "as_of_date": as_of_date,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "url": "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce",
            "archive": source.name,
            "sha256": file_sha256(source),
            "license": "CC BY-NC-SA 4.0",
        },
        "row_counts": counts,
        "database_bytes": output.stat().st_size,
        "build_seconds": round(elapsed, 2),
        "quality": {"foreign_key_violations": 0},
    }


def write_manifest(manifest: Path, payload: dict[str, Any]) -> None:
    manifest = manifest.resolve()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_name(manifest.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = build_database(args.source, args.output, replace=args.replace)
        write_manifest(args.manifest, payload)
    except Exception as exc:
        print(f"Olist import failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
