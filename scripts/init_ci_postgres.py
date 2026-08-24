"""Initialize a disposable PostgreSQL CI database with a small synthetic fixture."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = PROJECT_ROOT / "postgres" / "schema.sql"
SEMANTIC_SQL = PROJECT_ROOT / "postgres" / "semantic_tables.sql"
READONLY_GRANTS_SQL = PROJECT_ROOT / "postgres" / "readonly_grants.sql"
FIXTURE_SQL = PROJECT_ROOT / "tests" / "fixtures" / "postgres_ci.sql"

CI_BASE_ROW_COUNTS = {
    "geolocation": 3,
    "category_translations": 3,
    "customers": 4,
    "sellers": 3,
    "products": 4,
    "orders": 6,
    "order_items": 8,
    "payments": 7,
    "reviews": 5,
}
CI_SEMANTIC_ROW_COUNTS = {
    "order_financials": 6,
    "order_delivery_metrics": 4,
    "product_sales": 8,
    "category_sales_summary": 3,
    "delivery_kpis": 1,
    "payment_type_summary": 3,
    "customer_order_summary": 3,
}


def _read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _table_counts(connection: Any, tables: dict[str, int]) -> dict[str, int]:
    from psycopg import sql

    return {
        table: int(
            connection.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
        )
        for table in tables
    }


def initialize_ci_database(database_url: str, readonly_password: str) -> dict[str, Any]:
    """Reset an explicitly named *_ci database and load the deterministic fixture."""
    import psycopg
    from psycopg import sql

    if not readonly_password:
        raise RuntimeError("BI_CI_READONLY_PASSWORD is required")

    started = time.perf_counter()
    with psycopg.connect(database_url) as connection:
        database_name = str(connection.execute("SELECT current_database()").fetchone()[0])
        if not database_name.endswith("_ci"):
            raise RuntimeError("refusing to reset a database whose name does not end with '_ci'")

        role_exists = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_readonly')"
        ).fetchone()[0]
        role_action = "ALTER" if role_exists else "CREATE"
        connection.execute(
            sql.SQL(f"{role_action} ROLE agent_readonly LOGIN PASSWORD {{}}").format(
                sql.Literal(readonly_password)
            )
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO agent_readonly").format(
                sql.Identifier(database_name)
            )
        )

        connection.execute(_read_sql(SCHEMA_SQL))
        connection.execute(_read_sql(FIXTURE_SQL))
        connection.execute(_read_sql(SEMANTIC_SQL))
        connection.execute(_read_sql(READONLY_GRANTS_SQL))

        base_counts = _table_counts(connection, CI_BASE_ROW_COUNTS)
        semantic_counts = _table_counts(connection, CI_SEMANTIC_ROW_COUNTS)
        if base_counts != CI_BASE_ROW_COUNTS or semantic_counts != CI_SEMANTIC_ROW_COUNTS:
            raise RuntimeError("CI fixture row counts do not match the deterministic contract")

    return {
        "status": "ready",
        "database": database_name,
        "base_rows": base_counts,
        "semantic_rows": semantic_counts,
        "base_total": sum(base_counts.values()),
        "semantic_total": sum(semantic_counts.values()),
        "load_seconds": round(time.perf_counter() - started, 3),
    }


def main() -> int:
    database_url = os.getenv("BI_MIGRATION_DATABASE_URL", "")
    readonly_password = os.getenv("BI_CI_READONLY_PASSWORD", "")
    if not database_url:
        raise RuntimeError("BI_MIGRATION_DATABASE_URL is required")
    payload = initialize_ci_database(database_url, readonly_password)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
