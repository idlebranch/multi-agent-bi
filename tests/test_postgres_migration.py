from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.run_postgres_parity import _apply_comparison_overrides, load_postgres_gold
from benchmarks.schema import apply_evaluation_overrides, load_business_cases
from scripts.load_olist_postgres import BASE_TABLES, SEMANTIC_TABLES, TABLE_COLUMNS
from src.tools import postgres_db_tools


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES = PROJECT_ROOT / "benchmarks" / "cases" / "business_cases.json"
OVERRIDES = PROJECT_ROOT / "benchmarks" / "cases" / "evaluation_overrides.json"
POSTGRES_GOLD = PROJECT_ROOT / "benchmarks" / "cases" / "postgres_gold.json"
SCHEMA_SQL = PROJECT_ROOT / "postgres" / "schema.sql"
SEMANTIC_SQL = PROJECT_ROOT / "postgres" / "semantic_tables.sql"
GRANTS_SQL = PROJECT_ROOT / "postgres" / "readonly_grants.sql"


class PostgresMigrationContractTests(unittest.TestCase):
    def test_schema_has_all_base_and_semantic_tables(self) -> None:
        schema = SCHEMA_SQL.read_text(encoding="utf-8").casefold()
        semantic = SEMANTIC_SQL.read_text(encoding="utf-8").casefold()
        for table in BASE_TABLES:
            self.assertIn(f"create table {table}", schema)
            self.assertIn(table, TABLE_COLUMNS)
        for table in SEMANTIC_TABLES:
            self.assertIn(f"create table {table}", semantic)
        self.assertIn("price numeric(14, 2)", schema)
        self.assertIn("payment_value numeric(14, 2)", schema)
        self.assertIn("timestamp without time zone", schema)
        self.assertNotIn("julianday(", semantic)
        self.assertNotIn("strftime(", semantic)

    def test_postgres_gold_covers_exactly_85_query_cases(self) -> None:
        cases = apply_evaluation_overrides(load_business_cases(CASES), OVERRIDES)
        gold = load_postgres_gold(cases, POSTGRES_GOLD)
        self.assertEqual(len(gold), 85)
        self.assertFalse(
            any("strftime(" in sql.casefold() or "julianday(" in sql.casefold() for sql in gold.values())
        )
        payload = json.loads(POSTGRES_GOLD.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["portable_case_ids"]), 68)
        self.assertEqual(len(payload["queries"]), 17)

    def test_postgres_parity_applies_gold_comparison_overrides_to_both_sides(self) -> None:
        quarter_rows = [{"quarter": "2018-Q1", "order_count": 10}]
        self.assertEqual(
            _apply_comparison_overrides(
                {"comparison_gold_transform": "split_year_quarter"}, quarter_rows
            ),
            [{"year": "2018", "quarter": "Q1", "order_count": 10}],
        )
        projected_rows = [{"category_name": "books", "order_count": 2, "aov": 10.0}]
        self.assertEqual(
            _apply_comparison_overrides(
                {"comparison_gold_columns": ["category_name", "aov"]}, projected_rows
            ),
            [{"category_name": "books", "aov": 10.0}],
        )

    def test_application_policy_rejects_write_and_admin_sql(self) -> None:
        dangerous = (
            "INSERT INTO orders(order_id) VALUES ('x')",
            "UPDATE orders SET status='x'",
            "DELETE FROM orders",
            "CREATE TABLE probe(id INTEGER)",
            "DROP TABLE orders",
            "ALTER TABLE orders ADD COLUMN probe INTEGER",
            "WITH changed AS (DELETE FROM orders RETURNING *) SELECT * FROM changed",
            "COPY orders TO STDOUT",
            "SET statement_timeout = 0",
        )
        for sql in dangerous:
            with self.subTest(sql=sql):
                result = postgres_db_tools.validate_read_only_sql(sql)
                self.assertFalse(result["valid"])

    def test_readonly_grants_include_database_defense_in_depth(self) -> None:
        grants = GRANTS_SQL.read_text(encoding="utf-8").casefold()
        self.assertIn("grant select on all tables", grants)
        self.assertIn("revoke create on schema public", grants)
        self.assertIn("default_transaction_read_only = on", grants)
        self.assertIn("statement_timeout", grants)

    def test_database_url_label_never_exposes_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {"BI_DATABASE_URL": "postgresql://agent_readonly:very-secret@db:5433/warehouse"},
        ):
            label = postgres_db_tools.get_database_label()
        self.assertEqual(label, "db:5433/warehouse")
        self.assertNotIn("very-secret", label)


@unittest.skipUnless(
    os.getenv("BI_TEST_DATABASE_URL"),
    "PostgreSQL integration database is unavailable",
)
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = str(os.environ["BI_TEST_DATABASE_URL"])

    def test_schema_loading_row_counts_and_introspection(self) -> None:
        tables = postgres_db_tools.list_tables(self.database_url)
        self.assertTrue(set((*BASE_TABLES, *SEMANTIC_TABLES)).issubset(tables))
        overview = postgres_db_tools.get_db_overview(self.database_url)
        self.assertIn("PostgreSQL catalog", overview)
        self.assertIn("order_id text PK", overview)
        self.assertIn("order_id -> orders.order_id", overview)
        with patch.dict(os.environ, {"BI_DATABASE_URL": self.database_url}):
            health = postgres_db_tools.get_database_health_summary(force_refresh=True)
        self.assertEqual(health["table_counts"]["orders"], 99_441)
        self.assertEqual(health["table_counts"]["order_items"], 112_650)
        self.assertTrue(health["read_only"])

    def test_select_explain_invalid_sql_and_max_rows(self) -> None:
        validation = postgres_db_tools.validate_sql(
            "SELECT status, COUNT(*) FROM orders GROUP BY status", self.database_url
        )
        self.assertTrue(validation["valid"], validation["error"])
        invalid = postgres_db_tools.validate_sql(
            "SELECT missing_column FROM orders", self.database_url
        )
        self.assertFalse(invalid["valid"])
        result = postgres_db_tools.execute_sql(
            "SELECT generate_series(1, 5) AS n", self.database_url, max_rows=2
        )
        self.assertTrue(result["success"], result["error"])
        self.assertEqual(result["row_count"], 2)
        self.assertTrue(result["truncated"])

    def test_statement_timeout(self) -> None:
        result = postgres_db_tools.execute_sql(
            "SELECT pg_sleep(0.2)", self.database_url, timeout_seconds=0.01
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "query_timeout")

    def test_database_role_rejects_writes_even_without_validator(self) -> None:
        import psycopg

        statements = {
            "INSERT": (
                "INSERT INTO orders "
                "(order_id, customer_id, status, purchase_timestamp) "
                "VALUES ('readonly-probe', 'readonly-probe', 'created', NOW())"
            ),
            "UPDATE": "UPDATE orders SET status = status WHERE FALSE",
            "DELETE": "DELETE FROM orders WHERE FALSE",
            "CREATE": "CREATE TABLE readonly_probe(id INTEGER)",
            "DROP": "DROP TABLE orders",
            "ALTER": "ALTER TABLE orders ADD COLUMN readonly_probe INTEGER",
        }
        with psycopg.connect(self.database_url) as connection:
            self.assertEqual(connection.execute("SHOW default_transaction_read_only").fetchone()[0], "on")
            timeout_ms = connection.execute(
                "SELECT setting::integer FROM pg_settings WHERE name = 'statement_timeout'"
            ).fetchone()[0]
            self.assertGreater(timeout_ms, 0)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        for operation, statement in statements.items():
            with self.subTest(operation=operation), psycopg.connect(self.database_url) as connection:
                with self.assertRaises(psycopg.Error):
                    connection.execute(statement)


if __name__ == "__main__":
    unittest.main()
