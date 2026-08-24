from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.graph import app as stable_graph
from src.nodes.schema_linking import schema_linking_node
from src.nodes.sql_execution import sql_execution_node
from src.nodes.sql_generation import sql_generation_node
from src.nodes.sql_review import sql_review_node
from src.nodes.sql_validation import sql_validation_node
from src.state import create_initial_state
from src.workflow import run_graph_once


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self.content)


class OlistWorkflowIntegrationTests(unittest.TestCase):
    def _use_test_database(self) -> None:
        previous = os.environ.get("BI_DATABASE_URL")
        os.environ["BI_DATABASE_URL"] = os.environ["BI_TEST_DATABASE_URL"]

        def restore() -> None:
            if previous is None:
                os.environ.pop("BI_DATABASE_URL", None)
            else:
                os.environ["BI_DATABASE_URL"] = previous

        self.addCleanup(restore)

    @unittest.skipUnless(
        os.getenv("BI_TEST_DATABASE_URL"), "PostgreSQL integration database is unavailable"
    )
    def test_agent_stages_reach_a_real_olist_result(self) -> None:
        self._use_test_database()
        state = create_initial_state("已签收订单的平均客单价是多少？", as_of_date="2018-10-17")
        with (
            patch.dict(os.environ, {"BI_DATABASE_URL": os.environ["BI_TEST_DATABASE_URL"]}),
            patch(
                "src.nodes.schema_linking.get_llm",
                return_value=FakeLLM(
                    '{"tables":["order_financials"],'
                    '"columns":{"order_financials":["status","item_value"]},'
                    '"reasoning":"使用无连接放大的订单财务视图"}'
                ),
            ),
        ):
            state.update(schema_linking_node(state))

        self.assertEqual(state["schema_status"], "succeeded")
        self.assertEqual(state["relevant_tables"], ["order_financials"])

        sql = (
            "SELECT ROUND(SUM(item_value) / COUNT(*), 2) AS average_order_value "
            "FROM order_financials WHERE status = 'delivered'"
        )
        with patch(
            "src.nodes.sql_generation.get_llm",
            return_value=FakeLLM(sql),
        ):
            state.update(sql_generation_node(state))

        with patch(
            "src.nodes.sql_review.get_llm",
            return_value=FakeLLM(
                '{"approved":true,"summary":"口径和聚合正确","issues":[]}'
            ),
        ):
            state.update(sql_review_node(state))

        state.update(sql_validation_node(state))
        state.update(sql_execution_node(state))

        self.assertEqual(state["review_status"], "succeeded")
        self.assertEqual(state["validation_status"], "succeeded")
        self.assertEqual(state["execution_status"], "succeeded")
        self.assertEqual(str(state["sql_result"][0]["average_order_value"]), "137.04")

    @unittest.skipUnless(
        os.getenv("BI_TEST_DATABASE_URL"), "PostgreSQL integration database is unavailable"
    )
    def test_full_secured_graph_records_every_handoff(self) -> None:
        self._use_test_database()
        state = create_initial_state("已签收订单的平均客单价是多少？", as_of_date="2018-10-17")
        sql = (
            "SELECT ROUND(SUM(item_value) / COUNT(*), 2) AS average_order_value "
            "FROM order_financials WHERE status = 'delivered'"
        )
        with (
            patch.dict(os.environ, {"BI_DATABASE_URL": os.environ["BI_TEST_DATABASE_URL"]}),
            patch(
                "src.nodes.schema_linking.get_llm",
                return_value=FakeLLM(
                    '{"tables":["order_financials"],'
                    '"columns":{"order_financials":["status","item_value"]},'
                    '"reasoning":"使用订单财务视图"}'
                ),
            ),
            patch("src.nodes.sql_generation.get_llm", return_value=FakeLLM(sql)),
            patch(
                "src.nodes.sql_review.get_llm",
                return_value=FakeLLM(
                    '{"approved":true,"summary":"口径正确","issues":[]}'
                ),
            ),
            patch(
                "src.nodes.format_answer.get_llm",
                return_value=FakeLLM("已签收订单的平均客单价为 137.04。"),
            ),
        ):
            final_state, _ = run_graph_once(stable_graph, state)

        self.assertEqual(final_state["execution_status"], "succeeded")
        self.assertEqual(final_state["final_answer"], "已签收订单的平均客单价为 137.04。")
        self.assertEqual(len(final_state["sql_attempt_history"]), 1)
        self.assertEqual(
            [event["to_agent"] for event in final_state["handoff_history"]],
            [
                "schema_linking",
                "sql_generation",
                "sql_review",
                "sql_validation",
                "sql_execution",
                "format_answer",
            ],
        )
        self.assertTrue(all(item["allowed"] for item in final_state["policy_decisions"]))
