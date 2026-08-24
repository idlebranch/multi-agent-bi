"""Read-only, bounded PostgreSQL access and compact schema catalog helpers."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import urlsplit

from src.config import (
    DB_MAX_CONCURRENCY,
    DB_QUEUE_TIMEOUT_SECONDS,
    MAX_RESULT_ROWS,
    SCHEMA_DETAIL_MAX_TABLES,
    SCHEMA_MAX_TABLES,
    SCHEMA_SAMPLE_ROWS,
    SQL_TIMEOUT_SECONDS,
    get_active_dataset_manifest,
    get_database_url,
)
from src.policy import policy_limit


FORBIDDEN_SQL_TOKENS = {
    "ALTER",
    "ANALYZE",
    "ATTACH",
    "CALL",
    "COMMENT",
    "COPY",
    "CREATE",
    "DELETE",
    "DETACH",
    "DO",
    "DROP",
    "EXECUTE",
    "GRANT",
    "INSERT",
    "LOAD_EXTENSION",
    "LOCK",
    "MERGE",
    "PRAGMA",
    "REFRESH",
    "REINDEX",
    "REPLACE",
    "RESET",
    "REVOKE",
    "SET",
    "TRUNCATE",
    "UPDATE",
    "VACUUM",
}

_EFFECTIVE_DB_CONCURRENCY = min(
    DB_MAX_CONCURRENCY,
    int(policy_limit("database_concurrency", 4)),
)
_EFFECTIVE_QUEUE_TIMEOUT = min(
    DB_QUEUE_TIMEOUT_SECONDS,
    float(policy_limit("database_queue_timeout_seconds", 10)),
)
_EXECUTION_SEMAPHORE = threading.BoundedSemaphore(_EFFECTIVE_DB_CONCURRENCY)
_HEALTH_CACHE_LOCK = threading.Lock()
_HEALTH_CACHE: dict[str, Any] = {"key": None, "expires_at": 0.0, "payload": None}


def _load_psycopg() -> tuple[Any, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ModuleNotFoundError as exc:
        raise RuntimeError('PostgreSQL support requires the "psycopg[binary]" package') from exc
    return psycopg, dict_row


def get_database_label(database_url: str | None = None) -> str:
    """Return a credential-free database label suitable for diagnostics."""
    parsed = urlsplit(get_database_url(database_url))
    database = parsed.path.lstrip("/") or "postgres"
    return f"{parsed.hostname or 'localhost'}:{parsed.port or 5432}/{database}"


@contextmanager
def readonly_connection(
    database_url: str | None = None,
    *,
    timeout_seconds: float = SQL_TIMEOUT_SECONDS,
) -> Iterator[Any]:
    """Open a PostgreSQL read-only transaction and always close it."""
    psycopg, dict_row = _load_psycopg()
    timeout_ms = max(1, int(timeout_seconds * 1000))
    with psycopg.connect(
        get_database_url(database_url),
        connect_timeout=max(1, int(timeout_seconds)),
        row_factory=dict_row,
    ) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{timeout_ms}ms",),
            )
            yield conn


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_names(conn: Any) -> list[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema() "
        "AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY table_name"
    ).fetchall()
    return [str(row["table_name"]) for row in rows]


def get_database_health_summary(
    *, force_refresh: bool = False, cache_seconds: float = 300.0
) -> dict[str, Any]:
    """Return cached PostgreSQL connectivity, role, and warehouse diagnostics."""
    database_url = get_database_url()
    now = time.monotonic()
    with _HEALTH_CACHE_LOCK:
        if (
            not force_refresh
            and _HEALTH_CACHE["key"] == database_url
            and now < float(_HEALTH_CACHE["expires_at"])
            and isinstance(_HEALTH_CACHE["payload"], dict)
        ):
            return dict(_HEALTH_CACHE["payload"])

        with readonly_connection(database_url) as conn:
            system = conn.execute(
                "SELECT current_database() AS database_name, current_user AS database_user, "
                "current_setting('transaction_read_only') AS transaction_read_only, "
                "current_setting('statement_timeout') AS statement_timeout, "
                "current_setting('server_version') AS server_version, "
                "pg_database_size(current_database()) AS database_bytes"
            ).fetchone()
            tables = set(_table_names(conn))
            table_counts = _counts_for_existing(
                conn, tables, ("orders", "order_items", "payments", "reviews")
            )
            semantic_counts = _counts_for_existing(
                conn,
                tables,
                (
                    "order_financials",
                    "order_delivery_metrics",
                    "product_sales",
                    "category_sales_summary",
                    "delivery_kpis",
                    "payment_type_summary",
                    "customer_order_summary",
                ),
            )
            date_range: list[Any] = [None, None]
            if "orders" in tables:
                row = conn.execute(
                    "SELECT MIN(purchase_timestamp)::date AS minimum_date, "
                    "MAX(purchase_timestamp)::date AS maximum_date FROM orders"
                ).fetchone()
                date_range = [row["minimum_date"], row["maximum_date"]]

        database_bytes = int(system["database_bytes"])
        payload = {
            "status": "ready",
            "backend": "postgresql",
            "database": str(system["database_name"]),
            "database_label": get_database_label(database_url),
            "database_user": str(system["database_user"]),
            "server_version": str(system["server_version"]),
            "bytes": database_bytes,
            "size_mib": round(database_bytes / 1024 / 1024, 1),
            "read_only": str(system["transaction_read_only"]).casefold() == "on",
            "statement_timeout": str(system["statement_timeout"]),
            "date_range": date_range,
            "table_counts": table_counts,
            "semantic_table_counts": semantic_counts,
            "checked_at_epoch": time.time(),
            "cache_seconds": cache_seconds,
        }
        _HEALTH_CACHE.update(
            {
                "key": database_url,
                "expires_at": now + max(1.0, cache_seconds),
                "payload": payload,
            }
        )
        return dict(payload)


def _counts_for_existing(
    conn: Any, existing: set[str], requested: Sequence[str]
) -> dict[str, int]:
    return {
        table: int(
            conn.execute(
                f"SELECT COUNT(*) AS count FROM {_quote_identifier(table)}"
            ).fetchone()["count"]
        )
        for table in requested
        if table in existing
    }


def _mask_literals_comments_and_identifiers(sql: str) -> str:
    """Mask quoted text and comments while preserving statement punctuation."""
    output: list[str] = []
    index = 0
    while index < len(sql):
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < len(sql) else ""
        if char == "-" and nxt == "-":
            output.extend("  ")
            index += 2
            while index < len(sql) and sql[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and nxt == "*":
            output.extend("  ")
            index += 2
            while index < len(sql):
                if sql[index] == "*" and index + 1 < len(sql) and sql[index + 1] == "/":
                    output.extend("  ")
                    index += 2
                    break
                output.append(" ")
                index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            output.append(" ")
            index += 1
            while index < len(sql):
                output.append(" ")
                if sql[index] == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        output.append(" ")
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if char == "[":
            output.append(" ")
            index += 1
            while index < len(sql):
                output.append(" ")
                if sql[index] == "]":
                    index += 1
                    break
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def validate_read_only_sql(sql: str) -> dict[str, Any]:
    """Apply a conservative single-statement SELECT/CTE allow-list."""
    if not sql or not sql.strip():
        return {"valid": False, "error": "SQL is empty"}
    masked = _mask_literals_comments_and_identifiers(sql).strip()
    statement = masked[:-1].rstrip() if masked.endswith(";") else masked
    if ";" in statement:
        return {"valid": False, "error": "multiple SQL statements are not allowed"}
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", statement.upper())
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        return {"valid": False, "error": "only SELECT or WITH ... SELECT is allowed"}
    forbidden = sorted(FORBIDDEN_SQL_TOKENS.intersection(tokens))
    if forbidden:
        return {"valid": False, "error": f"forbidden SQL keyword: {forbidden[0]}"}
    if tokens[0] == "WITH" and "SELECT" not in tokens:
        return {"valid": False, "error": "WITH statements must end in a SELECT query"}
    return {"valid": True, "error": None}


def validate_sql(sql: str, database_url: str | None = None) -> dict[str, Any]:
    safety = validate_read_only_sql(sql)
    if not safety["valid"]:
        return safety
    try:
        with readonly_connection(database_url) as conn:
            conn.execute(f"EXPLAIN (FORMAT JSON) {sql}")
        return {"valid": True, "error": None}
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


def _execution_error(error: str, error_code: str) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "error": error,
        "error_code": error_code,
        "row_count": 0,
        "truncated": False,
    }


def execute_sql(
    sql: str,
    database_url: str | None = None,
    *,
    max_rows: int = MAX_RESULT_ROWS,
    timeout_seconds: float = SQL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute a read-only PostgreSQL query with timeout and bounded results."""
    safety = validate_read_only_sql(sql)
    if not safety["valid"]:
        return _execution_error(str(safety["error"]), "invalid_sql")
    if not _EXECUTION_SEMAPHORE.acquire(timeout=_EFFECTIVE_QUEUE_TIMEOUT):
        return _execution_error("database execution queue is full", "queue_timeout")
    try:
        psycopg, _ = _load_psycopg()
        with readonly_connection(database_url, timeout_seconds=timeout_seconds) as conn:
            cursor = conn.execute(sql)
            rows = cursor.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            data = [dict(row) for row in rows[:max_rows]]
        return {
            "success": True,
            "data": data,
            "error": None,
            "error_code": None,
            "row_count": len(data),
            "truncated": truncated,
        }
    except Exception as exc:
        if "psycopg" not in locals():
            return _execution_error(str(exc), "database_unavailable")
        if isinstance(exc, psycopg.errors.QueryCanceled):
            return _execution_error("query timed out", "query_timeout")
        if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
            return _execution_error(str(exc), "database_unavailable")
        if isinstance(exc, psycopg.Error):
            return _execution_error(str(exc), "database_error")
        if isinstance(exc, RuntimeError):
            return _execution_error(str(exc), "database_unavailable")
        return _execution_error(str(exc), "database_error")
    finally:
        _EXECUTION_SEMAPHORE.release()


def _load_semantic_model() -> dict[str, Any]:
    configured = os.getenv("BI_SEMANTIC_MODEL")
    if configured:
        path = Path(configured).expanduser().resolve()
    else:
        manifest, manifest_path = get_active_dataset_manifest()
        semantic_model = manifest.get("semantic_model")
        if not semantic_model:
            return {}
        path = (manifest_path.parent / str(semantic_model)).resolve()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _semantic_description(model: dict[str, Any], table: str) -> str:
    tables = model.get("tables", {})
    table_model = tables.get(table, {}) if isinstance(tables, dict) else {}
    return str(table_model.get("description", "")).strip() if isinstance(table_model, dict) else ""


def _column_rows(conn: Any, table: str) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            "SELECT c.column_name, c.data_type, c.is_nullable, EXISTS ("
            "SELECT 1 FROM pg_catalog.pg_constraint pc "
            "JOIN pg_catalog.pg_class relation ON relation.oid = pc.conrelid "
            "JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace "
            "JOIN LATERAL unnest(pc.conkey) AS key_column(attnum) ON TRUE "
            "JOIN pg_catalog.pg_attribute attribute "
            "ON attribute.attrelid = pc.conrelid AND attribute.attnum = key_column.attnum "
            "WHERE pc.contype = 'p' AND namespace.nspname = c.table_schema "
            "AND relation.relname = c.table_name "
            "AND attribute.attname = c.column_name) AS is_primary_key "
            "FROM information_schema.columns c "
            "WHERE c.table_schema = current_schema() AND c.table_name = %s "
            "ORDER BY c.ordinal_position",
            (table,),
        ).fetchall()
    )


def _foreign_key_rows(conn: Any, table: str) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            "SELECT local_attribute.attname AS column_name, "
            "foreign_relation.relname AS foreign_table_name, "
            "foreign_attribute.attname AS foreign_column_name "
            "FROM pg_catalog.pg_constraint pc "
            "JOIN pg_catalog.pg_class local_relation ON local_relation.oid = pc.conrelid "
            "JOIN pg_catalog.pg_namespace namespace "
            "ON namespace.oid = local_relation.relnamespace "
            "JOIN pg_catalog.pg_class foreign_relation "
            "ON foreign_relation.oid = pc.confrelid "
            "JOIN LATERAL unnest(pc.conkey, pc.confkey) WITH ORDINALITY "
            "AS key_columns(local_attnum, foreign_attnum, position) ON TRUE "
            "JOIN pg_catalog.pg_attribute local_attribute "
            "ON local_attribute.attrelid = pc.conrelid "
            "AND local_attribute.attnum = key_columns.local_attnum "
            "JOIN pg_catalog.pg_attribute foreign_attribute "
            "ON foreign_attribute.attrelid = pc.confrelid "
            "AND foreign_attribute.attnum = key_columns.foreign_attnum "
            "WHERE pc.contype = 'f' AND namespace.nspname = current_schema() "
            "AND local_relation.relname = %s ORDER BY key_columns.position",
            (table,),
        ).fetchall()
    )


def _index_rows(conn: Any, table: str) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            "SELECT indexname FROM pg_catalog.pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = %s ORDER BY indexname",
            (table,),
        ).fetchall()
    )


def list_tables(database_url: str | None = None) -> list[str]:
    with readonly_connection(database_url) as conn:
        return _table_names(conn)


def get_table_columns(table: str, database_url: str | None = None) -> list[str]:
    with readonly_connection(database_url) as conn:
        if table not in _table_names(conn):
            return []
        return [str(row["column_name"]) for row in _column_rows(conn, table)]


def get_db_overview(
    database_url: str | None = None, *, max_tables: int = SCHEMA_MAX_TABLES
) -> str:
    """Return a compact PostgreSQL catalog for the schema-selection stage."""
    with readonly_connection(database_url) as conn:
        all_tables = _table_names(conn)
        model = _load_semantic_model()
        lines = [f"# PostgreSQL catalog ({len(all_tables)} tables/views)"]
        if len(all_tables) > max_tables:
            lines.append(f"# Showing the first {max_tables} tables; refine catalog settings if needed.")
        for table in all_tables[:max_tables]:
            columns = _column_rows(conn, table)
            column_text = ", ".join(
                f"{row['column_name']} {row['data_type']}"
                + (" PK" if row["is_primary_key"] else "")
                for row in columns
            )
            foreign_keys = _foreign_key_rows(conn, table)
            fk_text = ", ".join(
                f"{row['column_name']} -> {row['foreign_table_name']}.{row['foreign_column_name']}"
                for row in foreign_keys
            )
            suffix = f"; foreign keys: {fk_text}" if fk_text else ""
            description = _semantic_description(model, table)
            prefix = f"{description}; " if description else ""
            lines.append(f"- {table}: {prefix}columns=({column_text}){suffix}")
        metrics = model.get("metrics", {})
        if isinstance(metrics, dict) and metrics:
            lines.append("# Governed business metrics")
            lines.extend(f"- {name}: {definition}" for name, definition in metrics.items())
        return "\n".join(lines)


def _expand_related_tables(conn: Any, selected_tables: Sequence[str]) -> list[str]:
    all_tables = _table_names(conn)
    selected = {table for table in selected_tables if table in all_tables}
    related = set(selected)
    for table in all_tables:
        for foreign_key in _foreign_key_rows(conn, table):
            parent = str(foreign_key["foreign_table_name"])
            if table in selected or parent in selected:
                related.update((table, parent))
    return [table for table in all_tables if table in related]


def get_db_schema(
    include_samples: bool = False,
    *,
    table_names: Sequence[str] | None = None,
    include_related: bool = False,
    sample_rows: int = SCHEMA_SAMPLE_ROWS,
    database_url: str | None = None,
) -> str:
    """Return detailed schema only for selected tables and join neighbors."""
    with readonly_connection(database_url) as conn:
        all_tables = _table_names(conn)
        model = _load_semantic_model()
        if table_names is None:
            selected = all_tables[:SCHEMA_DETAIL_MAX_TABLES]
        elif include_related:
            selected = _expand_related_tables(conn, table_names)
        else:
            requested = set(table_names)
            selected = [table for table in all_tables if table in requested]
        parts = ["# Selected PostgreSQL schema"]
        for table in selected[:SCHEMA_DETAIL_MAX_TABLES]:
            parts.append(f"\n## Table: {table}")
            description = _semantic_description(model, table)
            if description:
                parts.append(description)
            parts.extend(("| column | type | constraints |", "|---|---|---|"))
            columns = _column_rows(conn, table)
            for column in columns:
                constraints = []
                if column["is_primary_key"]:
                    constraints.append("PK")
                if column["is_nullable"] == "NO":
                    constraints.append("NOT NULL")
                parts.append(
                    f"| {column['column_name']} | {column['data_type']} | "
                    f"{', '.join(constraints)} |"
                )
            foreign_keys = _foreign_key_rows(conn, table)
            if foreign_keys:
                parts.append("\nForeign keys:")
                parts.extend(
                    f"- {table}.{row['column_name']} -> "
                    f"{row['foreign_table_name']}.{row['foreign_column_name']}"
                    for row in foreign_keys
                )
            indexes = _index_rows(conn, table)
            if indexes:
                parts.append("\nIndexes:")
                parts.extend(f"- {row['indexname']}" for row in indexes)

            tables_model = model.get("tables", {})
            table_model = tables_model.get(table, {}) if isinstance(tables_model, dict) else {}
            notes = table_model.get("columns", {}) if isinstance(table_model, dict) else {}
            if isinstance(notes, dict) and notes:
                parts.append("\nColumn meanings:")
                parts.extend(f"- {name}: {meaning}" for name, meaning in notes.items())

            if include_samples and sample_rows > 0:
                samples = conn.execute(
                    f"SELECT * FROM {_quote_identifier(table)} LIMIT %s", (sample_rows,)
                ).fetchall()
                if samples:
                    names = [str(column["column_name"]) for column in columns]
                    parts.extend(
                        (
                            "\nSample rows:",
                            "| " + " | ".join(names) + " |",
                            "|" + "|".join(["---"] * len(names)) + "|",
                        )
                    )
                    parts.extend(
                        "| " + " | ".join(str(row[name]) for name in names) + " |"
                        for row in samples
                    )
        metrics = model.get("metrics", {})
        if isinstance(metrics, dict) and metrics:
            parts.append("\n# Governed business metrics")
            parts.extend(f"- {name}: {definition}" for name, definition in metrics.items())
        return "\n".join(parts)
