from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.nodes.sql_execution import sql_execution_node
from src.nodes.sql_generation import sql_generation_node
from src.nodes.sql_review import sql_review_node
from src.state import create_initial_state


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self.content)


class NodeLifecycleTests(unittest.TestCase):
    def test_new_sql_invalidates_review_validation_and_execution(self) -> None:
        state = create_initial_state("count orders", as_of_date="2026-07-17")
        state.update(
            {
                "relevant_tables": ["orders"],
                "relevant_columns": {"orders": ["id"]},
                "schema_status": "succeeded",
                "review_status": "succeeded",
                "sql_validated": True,
                "validation_status": "succeeded",
                "execution_status": "failed",
                "error": "old execution error",
                "error_source": "sql_execution",
            }
        )
        with (
            patch("src.nodes.sql_generation.get_db_schema", return_value="schema"),
            patch(
                "src.nodes.sql_generation.get_llm",
                return_value=FakeLLM("SELECT COUNT(*) AS count FROM orders"),
            ),
        ):
            result = sql_generation_node(state)

        self.assertEqual(result["sql_status"], "succeeded")
        self.assertEqual(result["review_status"], "not_started")
        self.assertEqual(result["validation_status"], "not_started")
        self.assertFalse(result["sql_validated"])
        self.assertEqual(result["execution_status"], "not_started")
        self.assertEqual(result["sql_result"], [])

    def test_reviewer_rejection_becomes_writer_feedback(self) -> None:
        state = create_initial_state("revenue", as_of_date="2026-07-17")
        state.update(
            {
                "relevant_tables": ["orders"],
                "schema_status": "succeeded",
                "sql": "SELECT COUNT(*) FROM orders",
                "sql_status": "succeeded",
            }
        )
        with (
            patch("src.nodes.sql_review.get_db_schema", return_value="schema"),
            patch(
                "src.nodes.sql_review.get_llm",
                return_value=FakeLLM(
                    '{"approved":false,"summary":"COUNT is not revenue",'
                    '"issues":[{"code":"wrong_metric","severity":"high",'
                    '"message":"COUNT is not revenue"}]}'
                ),
            ),
        ):
            result = sql_review_node(state)

        self.assertEqual(result["review_status"], "failed")
        self.assertEqual(result["error_source"], "sql_review")
        self.assertIn("not revenue", result["review_feedback"])
        self.assertEqual(result["review_issues"][0]["code"], "wrong_metric")

    def test_ambiguous_reviewer_feedback_becomes_clarification(self) -> None:
        state = create_initial_state("2016年未签收商品情况", as_of_date="2018-10-17")
        state.update(
            {
                "relevant_tables": ["orders", "order_items"],
                "schema_status": "succeeded",
                "sql": "SELECT COUNT(*) FROM orders",
                "sql_status": "succeeded",
            }
        )
        message = (
            "‘未签收’和‘商品情况’存在多种统计口径，请明确订单状态、签收时间，"
            "以及订单数或商品件数。"
        )
        response = (
            '{"approved":false,"summary":"需要澄清",'
            '"issues":[{"code":"ambiguous_intent","severity":"high",'
            '"message":"' + message + '"}]}'
        )
        with (
            patch("src.nodes.sql_review.get_db_schema", return_value="schema"),
            patch("src.nodes.sql_review.get_llm", return_value=FakeLLM(response)),
        ):
            result = sql_review_node(state)

        self.assertEqual(result["request_status"], "clarification_required")
        self.assertEqual(result["request_message"], message)
        self.assertEqual(result["review_status"], "failed")
        self.assertEqual(result["review_issues"][0]["code"], "ambiguous_intent")
        self.assertNotIn("execution_status", result)

    def test_unsafe_sql_takes_precedence_over_ambiguity_fallback(self) -> None:
        state = create_initial_state("2016年未签收商品情况", as_of_date="2018-10-17")
        state.update(
            {
                "relevant_tables": ["orders"],
                "schema_status": "succeeded",
                "sql": "SELECT COUNT(*) FROM orders",
                "sql_status": "succeeded",
            }
        )
        response = (
            '{"approved":false,"summary":"拒绝", "issues":['
            '{"code":"ambiguous_intent","severity":"high",'
            '"message":"请澄清统计口径"},'
            '{"code":"unsafe_sql","severity":"high",'
            '"message":"检测到不安全 SQL"}]}'
        )
        with (
            patch("src.nodes.sql_review.get_db_schema", return_value="schema"),
            patch("src.nodes.sql_review.get_llm", return_value=FakeLLM(response)),
        ):
            result = sql_review_node(state)

        self.assertEqual(result["review_status"], "failed")
        self.assertNotIn("request_status", result)
        self.assertEqual(result["review_issues"][0]["code"], "ambiguous_intent")

    def test_hard_semantic_review_overrides_llm_approval(self) -> None:
        state = create_initial_state(
            "已签收 GMV 最高的五个商品类别是什么？",
            as_of_date="2018-10-17",
        )
        state.update(
            {
                "relevant_tables": ["product_sales", "category_translations"],
                "schema_status": "succeeded",
                "sql": "SELECT ct.category_name_english, SUM(ps.price) "
                "FROM product_sales ps JOIN category_translations ct "
                "ON ps.category_name = ct.category_name "
                "WHERE ps.order_status = 'delivered' GROUP BY 1",
                "sql_status": "succeeded",
            }
        )
        with (
            patch("src.nodes.sql_review.get_db_schema", return_value="schema"),
            patch(
                "src.nodes.sql_review.get_llm",
                return_value=FakeLLM(
                    '{"approved":true,"summary":"looks correct","issues":[]}'
                ),
            ),
        ):
            result = sql_review_node(state)

        self.assertEqual(result["review_status"], "failed")
        self.assertEqual(result["review_issues"][0]["code"], "join_fanout")

    def test_governed_policy_can_overrule_hallucinated_reviewer_issue(self) -> None:
        state = create_initial_state("各支付方式的支付金额是多少？", as_of_date="2018-10-17")
        state.update(
            {
                "relevant_tables": ["payment_type_summary"],
                "schema_status": "succeeded",
                "sql": "SELECT payment_type, payment_value FROM payment_type_summary",
                "sql_status": "succeeded",
            }
        )
        with (
            patch("src.nodes.sql_review.get_db_schema", return_value="schema"),
            patch(
                "src.nodes.sql_review.get_llm",
                return_value=FakeLLM(
                    '{"approved":false,"summary":"status filter missing",'
                    '"issues":[{"code":"missing_status_filter","severity":"high",'
                    '"message":"A delivered status filter is required."}]}'
                ),
            ),
        ):
            result = sql_review_node(state)

        self.assertEqual(result["review_status"], "succeeded")
        self.assertEqual(result["review_issues"], [])

    def test_reviewer_truncates_overlong_provider_messages(self) -> None:
        state = create_initial_state("revenue", as_of_date="2026-07-17")
        state.update(
            {
                "relevant_tables": ["orders"],
                "schema_status": "succeeded",
                "sql": "SELECT COUNT(*) FROM orders",
                "sql_status": "succeeded",
            }
        )
        long_message = "x" * 800
        response = (
            '{"approved":false,"summary":"bad","issues":['
            '{"code":"wrong_metric","severity":"high","message":"'
            + long_message
            + '"}]}'
        )
        with (
            patch("src.nodes.sql_review.get_db_schema", return_value="schema"),
            patch("src.nodes.sql_review.get_llm", return_value=FakeLLM(response)),
        ):
            result = sql_review_node(state)

        self.assertEqual(result["review_status"], "failed")
        self.assertEqual(len(result["review_issues"][0]["message"]), 500)

    def test_empty_execution_is_a_successful_terminal_result(self) -> None:
        state = create_initial_state("no rows", as_of_date="2026-07-17")
        state["sql"] = "SELECT 1 WHERE 0"
        with patch(
            "src.nodes.sql_execution.execute_sql",
            return_value={
                "success": True,
                "data": [],
                "error": None,
                "row_count": 0,
                "truncated": False,
            },
        ):
            result = sql_execution_node(state)

        self.assertEqual(result["execution_status"], "succeeded")
        self.assertEqual(result["result_row_count"], 0)


if __name__ == "__main__":
    unittest.main()
