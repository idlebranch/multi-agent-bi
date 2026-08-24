"""Read-only, bounded SQLite access and scalable schema catalog helpers."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from src.config import (
    DB_MAX_CONCURRENCY,
    DB_QUEUE_TIMEOUT_SECONDS,
    MAX_RESULT_ROWS,
    SCHEMA_DETAIL_MAX_TABLES,
    SCHEMA_MAX_TABLES,
    SCHEMA_SAMPLE_ROWS,
    SQL_TIMEOUT_SECONDS,
    get_active_dataset_manifest,
)
from src.policy import policy_limit


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "mock_db.sqlite"

FORBIDDEN_SQL_TOKENS = {
    "ALTER",
    "ANALYZE",
    "ATTACH",
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "INSERT",
    "LOAD_EXTENSION",
    "PRAGMA",
    "REINDEX",
    "REPLACE",
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
_HEALTH_CACHE: dict[str, Any] = {
    "key": None,
    "expires_at": 0.0,
    "payload": None,
}


def get_db_path(db_path: str | Path | None = None) -> Path:
    configured = db_path or os.getenv("BI_DB_PATH")
    if configured:
        path = Path(configured)
    else:
        manifest, manifest_path = get_active_dataset_manifest()
        database = manifest.get("database")
        path = manifest_path.parent / str(database) if database else DEFAULT_DB_PATH
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"database file does not exist: {path}")
    return path


@contextmanager
def readonly_connection(
    db_path: str | Path | None = None,
    *,
    timeout_seconds: float = SQL_TIMEOUT_SECONDS,
) -> Iterator[sqlite3.Connection]:
    """Open SQLite in URI read-only mode and always close it."""
    path = get_db_path(db_path)
    uri = f"{path.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout_seconds)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


def get_database_health_summary(
    *,
    force_refresh: bool = False,
    cache_seconds: float = 300.0,
) -> dict[str, Any]:
    """Return cached, read-only database diagnostics for the demo UI."""
    path = get_db_path()
    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    now = time.monotonic()

    with _HEALTH_CACHE_LOCK:
        if (
            not force_refresh
            and _HEALTH_CACHE["key"] == cache_key
            and now < float(_HEALTH_CACHE["expires_at"])
            and isinstance(_HEALTH_CACHE["payload"], dict)
        ):
            return dict(_HEALTH_CACHE["payload"])

        with readonly_connection(path) as conn:
            tables = set(_table_names(conn))
            query_only = bool(conn.execute("PRAGMA query_only").fetchone()[0])
            integrity = str(conn.execute("PRAGMA quick_check(1)").fetchone()[0])
            foreign_key_violations = sum(
                1 for _ in conn.execute("PRAGMA foreign_key_check")
            )

            main_tables = ("orders", "order_items", "payments", "reviews")
            table_counts = {
                table: int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                    ).fetchone()[0]
                )
                for table in main_tables
                if table in tables
            }
            semantic_tables = (
                "order_financials",
                "order_delivery_metrics",
                "product_sales",
                "category_sales_summary",
                "delivery_kpis",
                "payment_type_summary",
                "customer_order_summary",
            )
            semantic_counts = {
                table: int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                    ).fetchone()[0]
                )
                for table in semantic_tables
                if table in tables
            }
            date_range = [None, None]
            if "orders" in tables:
                order_columns = {
                    str(row[1])
                    for row in conn.execute('PRAGMA table_info("orders")').fetchall()
                }
                if "purchase_timestamp" in order_columns:
                    row = conn.execute(
                        "SELECT date(MIN(purchase_timestamp)), "
                        "date(MAX(purchase_timestamp)) FROM orders"
                    ).fetchone()
                    date_range = [row[0], row[1]]

        payload = {
            "status": "ready",
            "file": path.name,
            "bytes": stat.st_size,
            "size_mib": round(stat.st_size / 1024 / 1024, 1),
            "read_only": query_only,
            "integrity_check": integrity,
            "foreign_key_violations": foreign_key_violations,
            "date_range": date_range,
            "table_counts": table_counts,
            "semantic_table_counts": semantic_counts,
            "checked_at_epoch": time.time(),
            "cache_seconds": cache_seconds,
        }
        _HEALTH_CACHE.update(
            {
                "key": cache_key,
                "expires_at": now + max(1.0, cache_seconds),
                "payload": payload,
            }
        )
        return dict(payload)


def _mask_literals_comments_and_identifiers(sql: str) -> str:
    """Mask quoted text and comments while preserving statement punctuation."""
    output: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < length else ""

        if char == "-" and nxt == "-":
            output.extend("  ")
            index += 2
            while index < length and sql[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue

        if char == "/" and nxt == "*":
            output.extend("  ")
            index += 2
            while index < length:
                if sql[index] == "*" and index + 1 < length and sql[index + 1] == "/":
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
            while index < length:
                output.append(" ")
                if sql[index] == quote:
                    if index + 1 < length and sql[index + 1] == quote:
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
            while index < length:
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
    without_trailing_semicolon = masked[:-1].rstrip() if masked.endswith(";") else masked
    if ";" in without_trailing_semicolon:
        return {"valid": False, "error": "multiple SQL statements are not allowed"}

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", without_trailing_semicolon.upper())
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        return {"valid": False, "error": "only SELECT or WITH ... SELECT is allowed"}

    forbidden = sorted(FORBIDDEN_SQL_TOKENS.intersection(tokens))
    if forbidden:
        return {"valid": False, "error": f"forbidden SQL keyword: {forbidden[0]}"}
    if tokens[0] == "WITH" and "SELECT" not in tokens:
        return {"valid": False, "error": "WITH statements must end in a SELECT query"}

    return {"valid": True, "error": None}


def validate_sql(sql: str, db_path: str | Path | None = None) -> dict[str, Any]:
    safety = validate_read_only_sql(sql)
    if not safety["valid"]:
        return safety

    try:
        with readonly_connection(db_path) as conn:
            conn.execute(f"EXPLAIN QUERY PLAN {sql}")
        return {"valid": True, "error": None}
    except (sqlite3.Error, FileNotFoundError) as exc:
        return {"valid": False, "error": str(exc)}


def execute_sql(
    sql: str,
    db_path: str | Path | None = None,
    *,
    max_rows: int = MAX_RESULT_ROWS,
    timeout_seconds: float = SQL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute a read-only query with timeout and bounded result materialization."""
    safety = validate_read_only_sql(sql)
    if not safety["valid"]:
        return {
            "success": False,
            "data": None,
            "error": safety["error"],
            "error_code": "invalid_sql",
            "row_count": 0,
            "truncated": False,
        }

    acquired = _EXECUTION_SEMAPHORE.acquire(timeout=_EFFECTIVE_QUEUE_TIMEOUT)
    if not acquired:
        return {
            "success": False,
            "data": None,
            "error": "database execution queue is full",
            "error_code": "queue_timeout",
            "row_count": 0,
            "truncated": False,
        }

    deadline = time.monotonic() + timeout_seconds

    try:
        with readonly_connection(db_path, timeout_seconds=timeout_seconds) as conn:
            conn.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0,
                10_000,
            )
            cursor = conn.execute(sql)
            column_names = [description[0] for description in cursor.description or []]
            rows = cursor.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
            data = [dict(zip(column_names, row)) for row in rows]
            conn.set_progress_handler(None, 0)
        return {
            "success": True,
            "data": data,
            "error": None,
            "error_code": None,
            "row_count": len(data),
            "truncated": truncated,
        }
    except sqlite3.OperationalError as exc:
        message = "query timed out" if "interrupted" in str(exc).lower() else str(exc)
        return {
            "success": False,
            "data": None,
            "error": message,
            "error_code": (
                "query_timeout" if message == "query timed out" else "database_error"
            ),
            "row_count": 0,
            "truncated": False,
        }
    except FileNotFoundError as exc:
        return {
            "success": False,
            "data": None,
            "error": str(exc),
            "error_code": "database_unavailable",
            "row_count": 0,
            "truncated": False,
        }
    except sqlite3.Error as exc:
        return {
            "success": False,
            "data": None,
            "error": str(exc),
            "error_code": "database_error",
            "row_count": 0,
            "truncated": False,
        }
    finally:
        _EXECUTION_SEMAPHORE.release()


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _load_semantic_model(db_path: str | Path | None = None) -> dict[str, Any]:
    configured = os.getenv("BI_SEMANTIC_MODEL")
    if configured:
        path = Path(configured).expanduser().resolve()
    else:
        manifest, manifest_path = get_active_dataset_manifest()
        semantic_model = manifest.get("semantic_model")
        database = manifest.get("database")
        if not semantic_model or not database:
            return {}
        active_database = (manifest_path.parent / str(database)).resolve()
        if get_db_path(db_path) != active_database:
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
    if not isinstance(table_model, dict):
        return ""
    return str(table_model.get("description", "")).strip()


def list_tables(db_path: str | Path | None = None) -> list[str]:
    with readonly_connection(db_path) as conn:
        return _table_names(conn)


def _pragma_rows(conn: sqlite3.Connection, pragma: str, table: str) -> list[sqlite3.Row]:
    quoted = _quote_identifier(table)
    return conn.execute(f"PRAGMA {pragma}({quoted})").fetchall()


def get_table_columns(table: str, db_path: str | Path | None = None) -> list[str]:
    with readonly_connection(db_path) as conn:
        if table not in _table_names(conn):
            return []
        return [str(row[1]) for row in _pragma_rows(conn, "table_info", table)]


def get_db_overview(
    db_path: str | Path | None = None,
    *,
    max_tables: int = SCHEMA_MAX_TABLES,
) -> str:
    """Return a compact all-table catalog for the schema-selection stage."""
    with readonly_connection(db_path) as conn:
        all_tables = _table_names(conn)
        semantic_model = _load_semantic_model(db_path)
        selected = all_tables[:max_tables]
        lines = [f"# Database catalog ({len(all_tables)} tables/views)"]
        if len(all_tables) > max_tables:
            lines.append(f"# Showing the first {max_tables} tables; refine catalog settings if needed.")

        for table in selected:
            columns = _pragma_rows(conn, "table_info", table)
            column_text = ", ".join(
                f"{row[1]} {row[2]}" + (" PK" if row[5] else "")
                for row in columns
            )
            foreign_keys = _pragma_rows(conn, "foreign_key_list", table)
            fk_text = ", ".join(f"{row[3]} -> {row[2]}.{row[4]}" for row in foreign_keys)
            suffix = f"; foreign keys: {fk_text}" if fk_text else ""
            description = _semantic_description(semantic_model, table)
            prefix = f"{description}; " if description else ""
            lines.append(f"- {table}: {prefix}columns=({column_text}){suffix}")

        metrics = semantic_model.get("metrics", {})
        if isinstance(metrics, dict) and metrics:
            lines.append("# Governed business metrics")
            for name, definition in metrics.items():
                lines.append(f"- {name}: {definition}")
        return "\n".join(lines)


def _expand_related_tables(
    conn: sqlite3.Connection,
    selected_tables: Sequence[str],
) -> list[str]:
    all_tables = _table_names(conn)
    selected = {table for table in selected_tables if table in all_tables}
    related = set(selected)

    for table in all_tables:
        for fk in _pragma_rows(conn, "foreign_key_list", table):
            parent = str(fk[2])
            if table in selected or parent in selected:
                related.add(table)
                related.add(parent)
    return [table for table in all_tables if table in related]


def get_db_schema(
    include_samples: bool = False,
    *,
    table_names: Sequence[str] | None = None,
    include_related: bool = False,
    sample_rows: int = SCHEMA_SAMPLE_ROWS,
    db_path: str | Path | None = None,
) -> str:
    """Return detailed schema only for selected tables and their join neighbors."""
    with readonly_connection(db_path) as conn:
        all_tables = _table_names(conn)
        semantic_model = _load_semantic_model(db_path)
        if table_names is None:
            selected = all_tables[:SCHEMA_DETAIL_MAX_TABLES]
        elif include_related:
            selected = _expand_related_tables(conn, table_names)
        else:
            requested = set(table_names)
            selected = [table for table in all_tables if table in requested]

        if len(selected) > SCHEMA_DETAIL_MAX_TABLES:
            selected = selected[:SCHEMA_DETAIL_MAX_TABLES]

        parts = ["# Selected database schema"]
        for table in selected:
            parts.append(f"\n## Table: {table}")
            description = _semantic_description(semantic_model, table)
            if description:
                parts.append(description)
            parts.append("| column | type | constraints |")
            parts.append("|---|---|---|")
            columns = _pragma_rows(conn, "table_info", table)
            for column in columns:
                constraints = []
                if column[5]:
                    constraints.append("PK")
                if column[3]:
                    constraints.append("NOT NULL")
                parts.append(f"| {column[1]} | {column[2]} | {', '.join(constraints)} |")

            foreign_keys = _pragma_rows(conn, "foreign_key_list", table)
            if foreign_keys:
                parts.append("\nForeign keys:")
                for fk in foreign_keys:
                    parts.append(f"- {table}.{fk[3]} -> {fk[2]}.{fk[4]}")

            indexes = _pragma_rows(conn, "index_list", table)
            if indexes:
                parts.append("\nIndexes:")
                for index in indexes:
                    parts.append(f"- {index[1]}")

            tables_model = semantic_model.get("tables", {})
            table_model = tables_model.get(table, {}) if isinstance(tables_model, dict) else {}
            column_notes = table_model.get("columns", {}) if isinstance(table_model, dict) else {}
            if isinstance(column_notes, dict) and column_notes:
                parts.append("\nColumn meanings:")
                for column_name, meaning in column_notes.items():
                    parts.append(f"- {column_name}: {meaning}")

            if include_samples and sample_rows > 0:
                quoted = _quote_identifier(table)
                samples = conn.execute(f"SELECT * FROM {quoted} LIMIT ?", (sample_rows,)).fetchall()
                if samples:
                    names = [str(column[1]) for column in columns]
                    parts.append("\nSample rows:")
                    parts.append("| " + " | ".join(names) + " |")
                    parts.append("|" + "|".join(["---"] * len(names)) + "|")
                    for row in samples:
                        parts.append("| " + " | ".join(str(value) for value in row) + " |")

        metrics = semantic_model.get("metrics", {})
        if isinstance(metrics, dict) and metrics:
            parts.append("\n# Governed business metrics")
            for name, definition in metrics.items():
                parts.append(f"- {name}: {definition}")

        return "\n".join(parts)
