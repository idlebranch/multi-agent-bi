from __future__ import annotations

import unittest
from unittest.mock import patch

from src.tools.db_tools import execute_sql, validate_read_only_sql


class DatabaseToolsPolicyTests(unittest.TestCase):
    def test_allows_select_and_cte(self) -> None:
        self.assertTrue(validate_read_only_sql("SELECT * FROM orders")["valid"])
        self.assertTrue(
            validate_read_only_sql(
                "WITH totals AS (SELECT SUM(price) AS total FROM order_items) "
                "SELECT * FROM totals"
            )["valid"]
        )

    def test_rejects_writes_admin_and_multiple_statements(self) -> None:
        for sql in (
            "DELETE FROM orders",
            "TRUNCATE TABLE orders",
            "COPY orders TO STDOUT",
            "SET statement_timeout = 0",
            "SELECT 1; SELECT 2",
        ):
            with self.subTest(sql=sql):
                self.assertFalse(validate_read_only_sql(sql)["valid"])

    def test_quoted_forbidden_words_are_not_treated_as_operations(self) -> None:
        result = validate_read_only_sql("SELECT 'DROP TABLE orders' AS example")
        self.assertTrue(result["valid"])

    def test_execution_returns_structured_error_codes(self) -> None:
        invalid = execute_sql("DELETE FROM orders")
        self.assertFalse(invalid["success"])
        self.assertEqual(invalid["error_code"], "invalid_sql")

        class FullQueue:
            def acquire(self, *, timeout):
                return False

        with patch("src.tools.postgres_db_tools._EXECUTION_SEMAPHORE", FullQueue()):
            queued = execute_sql("SELECT 1")
        self.assertFalse(queued["success"])
        self.assertEqual(queued["error_code"], "queue_timeout")


if __name__ == "__main__":
    unittest.main()
