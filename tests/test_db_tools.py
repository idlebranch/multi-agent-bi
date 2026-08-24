from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tools.db_tools import (
    execute_sql,
    get_database_health_summary,
    get_db_overview,
    get_db_schema,
    validate_read_only_sql,
    validate_sql,
)


class DatabaseToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        handle.close()
        self.db_path = Path(handle.name)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(
                """
                CREATE TABLE customers (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    customer_id INTEGER NOT NULL REFERENCES customers(id),
                    amount REAL NOT NULL
                );
                INSERT INTO customers VALUES (1, 'A'), (2, 'B');
                INSERT INTO orders VALUES
                    (1, 1, 10.0), (2, 1, 20.0), (3, 2, 30.0);
                """
            )
            conn.commit()
        finally:
            conn.close()
        self.previous_path = os.environ.get("BI_DB_PATH")
        os.environ["BI_DB_PATH"] = str(self.db_path)

    def tearDown(self) -> None:
        if self.previous_path is None:
            os.environ.pop("BI_DB_PATH", None)
        else:
            os.environ["BI_DB_PATH"] = self.previous_path
        self.db_path.unlink(missing_ok=True)

    def test_allows_select_and_cte(self) -> None:
        self.assertTrue(validate_read_only_sql("SELECT * FROM orders")["valid"])
        self.assertTrue(
            validate_read_only_sql(
                "WITH totals AS (SELECT SUM(amount) AS total FROM orders) SELECT * FROM totals"
            )["valid"]
        )

    def test_rejects_writes_admin_and_multiple_statements(self) -> None:
        for sql in (
            "DELETE FROM orders",
            "PRAGMA table_info(orders)",
            "ATTACH DATABASE 'other.db' AS other",
            "SELECT 1; SELECT 2",
        ):
            with self.subTest(sql=sql):
                self.assertFalse(validate_read_only_sql(sql)["valid"])

    def test_database_validation_checks_real_columns(self) -> None:
        self.assertTrue(validate_sql("SELECT amount FROM orders")["valid"])
        self.assertFalse(validate_sql("SELECT ghost FROM orders")["valid"])

    def test_execution_distinguishes_empty_results(self) -> None:
        result = execute_sql("SELECT * FROM orders WHERE id < 0")
        self.assertTrue(result["success"])
        self.assertEqual(result["row_count"], 0)
        self.assertEqual(result["data"], [])
        self.assertFalse(result["truncated"])

    def test_execution_caps_materialized_rows(self) -> None:
        result = execute_sql("SELECT * FROM orders ORDER BY id", max_rows=2)
        self.assertTrue(result["success"])
        self.assertEqual(result["row_count"], 2)
        self.assertTrue(result["truncated"])

    def test_execution_returns_structured_error_codes(self) -> None:
        invalid = execute_sql("DELETE FROM orders")
        self.assertFalse(invalid["success"])
        self.assertEqual(invalid["error_code"], "invalid_sql")

        class FullQueue:
            def acquire(self, *, timeout):
                return False

        with patch("src.tools.db_tools._EXECUTION_SEMAPHORE", FullQueue()):
            queued = execute_sql("SELECT 1")
        self.assertFalse(queued["success"])
        self.assertEqual(queued["error_code"], "queue_timeout")

    def test_catalog_exposes_foreign_keys_and_selected_detail(self) -> None:
        overview = get_db_overview()
        self.assertIn("orders", overview)
        self.assertIn("customer_id -> customers.id", overview)

        detail = get_db_schema(table_names=["orders"], include_related=True)
        self.assertIn("Table: orders", detail)
        self.assertIn("Table: customers", detail)
        self.assertIn("orders.customer_id -> customers.id", detail)

    def test_health_summary_is_read_only_and_cached(self) -> None:
        first = get_database_health_summary(force_refresh=True)
        second = get_database_health_summary()
        self.assertEqual(first["integrity_check"], "ok")
        self.assertTrue(first["read_only"])
        self.assertEqual(first["foreign_key_violations"], 0)
        self.assertEqual(first["table_counts"]["orders"], 3)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
