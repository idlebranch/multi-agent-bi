from __future__ import annotations

import os
import re
import unittest
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.init_ci_postgres import CI_BASE_ROW_COUNTS, CI_SEMANTIC_ROW_COUNTS
from scripts.load_olist_postgres import BASE_TABLES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "postgres_ci.sql"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


class CiPipelineContractTests(unittest.TestCase):
    def test_fixture_contains_data_but_no_competing_schema(self) -> None:
        fixture = FIXTURE.read_text(encoding="utf-8").casefold()

        for table in BASE_TABLES:
            self.assertIn(f"insert into {table}", fixture)
        self.assertNotIn("create table", fixture)
        self.assertEqual(sum(CI_BASE_ROW_COUNTS.values()), 43)
        self.assertEqual(sum(CI_SEMANTIC_ROW_COUNTS.values()), 28)

    def test_workflow_is_offline_from_live_llm_and_full_olist(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("push:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("image: postgres:17", workflow)
        self.assertIn("uv sync --locked", workflow)
        self.assertIn("uv run ruff check .", workflow)
        self.assertIn('-m "not live_llm"', workflow)
        self.assertIn("docker build", workflow)
        self.assertNotRegex(workflow, re.compile(r"(?m)^\s*DEEPSEEK_API_KEY:"))
        self.assertNotIn("olist.zip", workflow.casefold())


@pytest.mark.postgres
@unittest.skipUnless(
    os.getenv("BI_CI_FIXTURE") == "1" and os.getenv("BI_TEST_DATABASE_URL"),
    "the deterministic PostgreSQL CI fixture is unavailable",
)
class CiFixtureIntegrationTests(unittest.TestCase):
    def test_exact_base_and_semantic_row_counts(self) -> None:
        import psycopg
        from psycopg import sql

        with psycopg.connect(os.environ["BI_TEST_DATABASE_URL"]) as connection:
            for table, expected in {**CI_BASE_ROW_COUNTS, **CI_SEMANTIC_ROW_COUNTS}.items():
                with self.subTest(table=table):
                    actual = connection.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
                    ).fetchone()[0]
                    self.assertEqual(actual, expected)

    def test_governed_metric_values(self) -> None:
        import psycopg

        with psycopg.connect(os.environ["BI_TEST_DATABASE_URL"]) as connection:
            average_order_value = connection.execute(
                "SELECT ROUND(SUM(item_value) / COUNT(*), 2) "
                "FROM order_financials WHERE status = 'delivered'"
            ).fetchone()[0]
            on_time_percentage = connection.execute(
                "SELECT on_time_delivery_pct FROM delivery_kpis"
            ).fetchone()[0]

        self.assertEqual(average_order_value, Decimal("137.04"))
        self.assertEqual(on_time_percentage, Decimal("75.0000"))


if __name__ == "__main__":
    unittest.main()
