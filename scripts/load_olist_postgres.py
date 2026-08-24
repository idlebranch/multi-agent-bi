"""Load the Olist CSV bundle into the PostgreSQL production warehouse."""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.olist_source import (  # noqa: E402
    DEFAULT_SOURCE,
    SEMANTIC_MODEL,
    csv_rows,
    file_sha256,
    find_members,
    float_value,
    int_value,
    text_value,
    write_manifest,
)


DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "postgres_dataset_manifest.json"
SCHEMA_SQL = PROJECT_ROOT / "postgres" / "schema.sql"
SEMANTIC_SQL = PROJECT_ROOT / "postgres" / "semantic_tables.sql"
READONLY_GRANTS_SQL = PROJECT_ROOT / "postgres" / "readonly_grants.sql"

BASE_TABLES = (
    "category_translations",
    "geolocation",
    "customers",
    "sellers",
    "products",
    "orders",
    "order_items",
    "payments",
    "reviews",
)
SEMANTIC_TABLES = (
    "order_financials",
    "order_delivery_metrics",
    "product_sales",
    "category_sales_summary",
    "delivery_kpis",
    "payment_type_summary",
    "customer_order_summary",
)

TABLE_COLUMNS = {
    "category_translations": ("category_name", "category_name_english"),
    "geolocation": (
        "zip_code_prefix",
        "latitude",
        "longitude",
        "city",
        "state",
        "source_row_count",
    ),
    "customers": (
        "customer_id",
        "customer_unique_id",
        "zip_code_prefix",
        "city",
        "state",
    ),
    "sellers": ("seller_id", "zip_code_prefix", "city", "state"),
    "products": (
        "product_id",
        "category_name",
        "name_length",
        "description_length",
        "photos_qty",
        "weight_g",
        "length_cm",
        "height_cm",
        "width_cm",
    ),
    "orders": (
        "order_id",
        "customer_id",
        "status",
        "purchase_timestamp",
        "approved_at",
        "delivered_carrier_timestamp",
        "delivered_customer_timestamp",
        "estimated_delivery_timestamp",
    ),
    "order_items": (
        "order_id",
        "order_item_id",
        "product_id",
        "seller_id",
        "shipping_limit_timestamp",
        "price",
        "freight_value",
    ),
    "payments": (
        "order_id",
        "payment_sequential",
        "payment_type",
        "payment_installments",
        "payment_value",
    ),
    "reviews": (
        "review_id",
        "order_id",
        "review_score",
        "comment_title",
        "comment_message",
        "creation_timestamp",
        "answer_timestamp",
    ),
}


def _read_sql(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read migration SQL: {path}") from exc


def _geolocation_rows(
    rows: Iterable[dict[str, str]],
) -> tuple[int, list[Sequence[Any]]]:
    """Apply deterministic postal-prefix aggregation to source geolocation rows."""
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

    aggregated: list[Sequence[Any]] = []
    for prefix in sorted(coordinates):
        latitude_sum, longitude_sum, count = coordinates[prefix]
        city, state = locations[prefix].most_common(1)[0][0]
        aggregated.append(
            (prefix, latitude_sum / count, longitude_sum / count, city, state, int(count))
        )
    return raw_count, aggregated


def _source_rows(
    bundle: zipfile.ZipFile,
    members: dict[str, str],
) -> tuple[dict[str, Iterable[Sequence[Any]]], int]:
    geolocation_source_count, geolocation = _geolocation_rows(
        csv_rows(bundle, members["geolocation"])
    )
    return (
        {
            "category_translations": (
                (
                    text_value(row.get("product_category_name")),
                    text_value(row.get("product_category_name_english")),
                )
                for row in csv_rows(bundle, members["translations"])
            ),
            "geolocation": geolocation,
            "customers": (
                (
                    text_value(row.get("customer_id")),
                    text_value(row.get("customer_unique_id")),
                    int_value(row.get("customer_zip_code_prefix")),
                    text_value(row.get("customer_city")),
                    text_value(row.get("customer_state")),
                )
                for row in csv_rows(bundle, members["customers"])
            ),
            "sellers": (
                (
                    text_value(row.get("seller_id")),
                    int_value(row.get("seller_zip_code_prefix")),
                    text_value(row.get("seller_city")),
                    text_value(row.get("seller_state")),
                )
                for row in csv_rows(bundle, members["sellers"])
            ),
            "products": (
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
            "orders": (
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
            "order_items": (
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
            "payments": (
                (
                    text_value(row.get("order_id")),
                    int_value(row.get("payment_sequential")),
                    text_value(row.get("payment_type")),
                    int_value(row.get("payment_installments")),
                    float_value(row.get("payment_value")),
                )
                for row in csv_rows(bundle, members["payments"])
            ),
            "reviews": (
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
        },
        geolocation_source_count,
    )


def _copy_rows(conn: Any, table: str, rows: Iterable[Sequence[Any]]) -> int:
    from psycopg import sql

    columns = TABLE_COLUMNS[table]
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
    )
    count = 0
    with conn.cursor().copy(statement) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
            if count % 100_000 == 0:
                print(f"  {table}: {count:,} rows", flush=True)
    print(f"  {table}: {count:,} rows", flush=True)
    return count


def _table_counts(conn: Any, tables: Sequence[str]) -> dict[str, int]:
    from psycopg import sql

    counts: dict[str, int] = {}
    with conn.cursor() as cursor:
        for table in tables:
            cursor.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
            )
            counts[table] = int(cursor.fetchone()[0])
    return counts


def warehouse_is_initialized(database_url: str) -> bool:
    """Return whether the production base and semantic warehouse already contain data."""
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise RuntimeError('install the project dependency: psycopg[binary]') from exc

    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "SELECT to_regclass('public.orders') IS NOT NULL, "
            "to_regclass('public.order_financials') IS NOT NULL"
        ).fetchone()
        if not row or not all(bool(value) for value in row):
            return False
        return bool(conn.execute("SELECT EXISTS (SELECT 1 FROM orders)").fetchone()[0])


def build_database(
    source: Path,
    database_url: str,
    *,
    replace: bool,
    apply_readonly_grants: bool = True,
) -> dict[str, Any]:
    """Rebuild PostgreSQL atomically; any load or semantic-table error rolls back."""
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise RuntimeError('install the project dependency: psycopg[binary]') from exc

    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Olist archive not found: {source}")

    started = datetime.now(timezone.utc)
    counts: dict[str, int] = {}
    with zipfile.ZipFile(source) as bundle:
        members = find_members(bundle)
        rows_by_table, geolocation_source_count = _source_rows(bundle, members)
        print(f"Source archive verified: {len(members)} CSV files", flush=True)

        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name = 'orders')"
                )
                warehouse_exists = bool(cursor.fetchone()[0])
                if warehouse_exists and not replace:
                    raise FileExistsError(
                        "PostgreSQL warehouse already exists (pass --replace to rebuild)"
                    )
                cursor.execute(_read_sql(SCHEMA_SQL))

            for table in BASE_TABLES:
                counts[table] = _copy_rows(conn, table, rows_by_table[table])
            counts["geolocation_source"] = geolocation_source_count

            with conn.cursor() as cursor:
                cursor.execute(_read_sql(SEMANTIC_SQL))
            persisted_counts = _table_counts(conn, (*BASE_TABLES, *SEMANTIC_TABLES))
            for table in BASE_TABLES:
                if persisted_counts[table] != counts[table]:
                    raise RuntimeError(
                        f"row-count mismatch for {table}: "
                        f"copied={counts[table]} stored={persisted_counts[table]}"
                    )
            counts.update({table: persisted_counts[table] for table in SEMANTIC_TABLES})

            if apply_readonly_grants:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_roles "
                        "WHERE rolname = 'agent_readonly')"
                    )
                    if not bool(cursor.fetchone()[0]):
                        raise RuntimeError(
                            "agent_readonly role does not exist; initialize roles first"
                        )
                    cursor.execute(_read_sql(READONLY_GRANTS_SQL))

            with conn.cursor() as cursor:
                cursor.execute("SELECT MAX(purchase_timestamp)::date FROM orders")
                as_of_date = cursor.fetchone()[0].isoformat()

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return {
        "name": "olist_brazilian_ecommerce",
        "backend": "postgresql",
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
        "build_seconds": round(elapsed, 2),
        "quality": {"transactional_load": True, "row_counts_verified": True},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--database-url", default=os.getenv("BI_MIGRATION_DATABASE_URL"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replace", action="store_true")
    mode.add_argument(
        "--if-empty",
        action="store_true",
        help="load only when the PostgreSQL warehouse is not already initialized",
    )
    parser.add_argument("--skip-readonly-grants", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_url:
        print("Olist import failed: BI_MIGRATION_DATABASE_URL is not configured", file=sys.stderr)
        return 2
    try:
        if args.if_empty and warehouse_is_initialized(str(args.database_url)):
            print(json.dumps({"status": "reused", "backend": "postgresql"}, indent=2))
            return 0
        payload = build_database(
            args.source,
            str(args.database_url),
            replace=args.replace,
            apply_readonly_grants=not args.skip_readonly_grants,
        )
        write_manifest(args.manifest, payload)
    except Exception as exc:
        print(f"Olist import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
