"""Frozen read-only SQLite helpers used only for migration parity and history."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from src.config import get_active_dataset_manifest
from src.tools.db_tools import validate_read_only_sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_REFERENCE = PROJECT_ROOT / "data" / "olist.sqlite"


def get_sqlite_reference_path(db_path: str | Path | None = None) -> Path:
    configured = db_path or os.getenv("BI_SQLITE_REFERENCE_PATH")
    if configured:
        path = Path(configured)
    else:
        manifest, manifest_path = get_active_dataset_manifest()
        database = manifest.get("database")
        path = manifest_path.parent / str(database) if database else DEFAULT_SQLITE_REFERENCE
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite reference does not exist: {path}")
    return path


@contextmanager
def sqlite_reference_connection(
    db_path: str | Path | None = None, *, timeout_seconds: float = 30.0
) -> Iterator[sqlite3.Connection]:
    path = get_sqlite_reference_path(db_path)
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro", uri=True, timeout=timeout_seconds
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
    finally:
        connection.close()


def validate_sqlite(sql: str, db_path: str | Path | None = None) -> dict[str, Any]:
    safety = validate_read_only_sql(sql)
    if not safety["valid"]:
        return safety
    try:
        with sqlite_reference_connection(db_path) as connection:
            connection.execute(f"EXPLAIN QUERY PLAN {sql}")
        return {"valid": True, "error": None}
    except (sqlite3.Error, FileNotFoundError) as exc:
        return {"valid": False, "error": str(exc)}


def execute_sqlite(
    sql: str,
    db_path: str | Path | None = None,
    *,
    max_rows: int = 10_000,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    safety = validate_read_only_sql(sql)
    if not safety["valid"]:
        return _error(str(safety["error"]), "invalid_sql")
    try:
        with sqlite_reference_connection(db_path, timeout_seconds=timeout_seconds) as connection:
            cursor = connection.execute(sql)
            names = [str(column[0]) for column in cursor.description or []]
            rows = cursor.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            data = [dict(zip(names, row)) for row in rows[:max_rows]]
        return {
            "success": True,
            "data": data,
            "error": None,
            "error_code": None,
            "row_count": len(data),
            "truncated": truncated,
        }
    except FileNotFoundError as exc:
        return _error(str(exc), "database_unavailable")
    except sqlite3.Error as exc:
        return _error(str(exc), "database_error")


def _error(message: str, code: str) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "error": message,
        "error_code": code,
        "row_count": 0,
        "truncated": False,
    }


def _sqlite_table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def get_sqlite_db_overview(db_path: str | Path | None = None) -> str:
    """Expose the frozen reference catalog for SQLite-only migration tests."""
    with sqlite_reference_connection(db_path) as connection:
        lines = ["# SQLite reference catalog"]
        for table in _sqlite_table_names(connection):
            columns = connection.execute(
                f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            text = ", ".join(
                f"{column[1]} {column[2]}" + (" PK" if column[5] else "")
                for column in columns
            )
            foreign_keys = connection.execute(
                f'PRAGMA foreign_key_list("{table.replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            suffix = ", ".join(
                f"{foreign_key[3]} -> {foreign_key[2]}.{foreign_key[4]}"
                for foreign_key in foreign_keys
            )
            lines.append(f"- {table}: columns=({text})" + (f"; foreign keys: {suffix}" if suffix else ""))
        return "\n".join(lines)


def get_sqlite_db_schema(
    *,
    table_names: Sequence[str],
    include_related: bool = False,
    db_path: str | Path | None = None,
) -> str:
    """Return compact schema detail from the frozen SQLite reference."""
    with sqlite_reference_connection(db_path) as connection:
        all_tables = _sqlite_table_names(connection)
        selected = {table for table in table_names if table in all_tables}
        if include_related:
            for table in all_tables:
                foreign_keys = connection.execute(
                    f'PRAGMA foreign_key_list("{table.replace(chr(34), chr(34) * 2)}")'
                ).fetchall()
                for foreign_key in foreign_keys:
                    parent = str(foreign_key[2])
                    if table in selected or parent in selected:
                        selected.update((table, parent))
        parts = ["# Selected SQLite reference schema"]
        for table in all_tables:
            if table not in selected:
                continue
            parts.append(f"## Table: {table}")
            for foreign_key in connection.execute(
                f'PRAGMA foreign_key_list("{table.replace(chr(34), chr(34) * 2)}")'
            ).fetchall():
                parts.append(
                    f"- {table}.{foreign_key[3]} -> {foreign_key[2]}.{foreign_key[4]}"
                )
        return "\n".join(parts)


def sqlite_database_fingerprint(
    db_path: str | Path | None = None, *, tables: Sequence[str] = ()
) -> dict[str, Any]:
    path = get_sqlite_reference_path(db_path)
    with sqlite_reference_connection(path) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violations = sum(
            1 for _ in connection.execute("PRAGMA foreign_key_check")
        )
        present = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        row_counts = {
            table: int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                ).fetchone()[0]
            )
            for table in tables
            if table in present
        }
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
        "integrity_check": integrity,
        "foreign_key_violations": foreign_key_violations,
        "row_counts": row_counts,
        "read_only_connection": True,
    }
