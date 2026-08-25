"""Deterministic regression tests for the P0 stability fixes.

Covers:
- sql_review terminal_reason policy contract
- governed undelivered/未签收 semantic concept
- dataset date coverage gate (pre-SQL termination for out-of-range years)
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.nodes.sql_review import sql_review_node
from src.routing import decide_next_node
from src.semantic_rules import (
    identify_metric,
    partial_date_coverage_note,
    preferred_tables_for_question,
    question_requests_delivered_scope,
    question_requests_undelivered_scope,
    undelivered_metric_is_ambiguous,
)
from src.state import create_initial_state
from src.workflow import with_visit_tracking


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self.content)


class P0PolicyRegressionTests(unittest.TestCase):
    def test_reviewer_clarification_survives_policy_wrapper(self) -> None:
        state = create_initial_state("2016年未签收商品情况", as_of_date="2018-10-17")
        state.update(
            {
                "relevant_tables": ["orders"],
                "schema_status": "succeeded",
                "sql": "SELECT COUNT(*) FROM orders",
                "sql_status": "succeeded",
            }
        )
        message = "请明确统计口径：订单数、商品件数还是商品金额。"
        response = (
            '{"approved":false,"summary":"需要澄清",'
            '"issues":[{"code":"ambiguous_intent","severity":"high",'
            '"message":"' + message + '"}]}'
        )
        wrapped = with_visit_tracking(sql_review_node, "sql_review")
        with (
            patch("src.nodes.sql_review.get_db_schema", return_value="schema"),
            patch("src.nodes.sql_review.get_llm", return_value=FakeLLM(response)),
        ):
            result = wrapped(state)

        self.assertEqual(result["review_status"], "failed")
        self.assertEqual(result["request_status"], "clarification_required")
        self.assertEqual(result["request_message"], message)
        self.assertIn("terminal_reason", result)
        self.assertTrue(
            any(issue["code"] == "ambiguous_intent" for issue in result["review_issues"])
        )
        self.assertNotIn("policy guard rejected", result.get("review_feedback", ""))
        self.assertNotIn("unauthorized state fields", result.get("review_feedback", ""))


class P0DateCoverageTests(unittest.TestCase):
    def test_years_before_dataset_start_terminate_before_schema(self) -> None:
        for question in ("2014年订单情况", "2015年订单情况"):
            with self.subTest(question=question):
                state = create_initial_state(question, as_of_date="2018-10-17")
                self.assertEqual(state["request_status"], "out_of_scope")
                self.assertIn("2016-09-04", state["request_message"])
                self.assertEqual(decide_next_node(state).next_node, "format_answer")
                self.assertEqual(state["execution_status"], "not_started")
                self.assertEqual(state["sql"], "")

    def test_partial_and_later_years_are_not_terminated(self) -> None:
        for question in ("2016年订单情况", "2018年订单情况", "2030年每月有多少订单？"):
            with self.subTest(question=question):
                state = create_initial_state(question, as_of_date="2018-10-17")
                self.assertNotEqual(state["request_status"], "out_of_scope")

    def test_partial_coverage_note_marks_partial_years(self) -> None:
        self.assertIn("2016-09-04", partial_date_coverage_note("2016年订单情况"))
        self.assertIn("2018-10-17", partial_date_coverage_note("2018年订单情况"))
        self.assertEqual(partial_date_coverage_note("2017年订单情况"), "")


class P0UndeliveredTests(unittest.TestCase):
    def test_undelivered_synonyms_map_to_one_concept(self) -> None:
        for question in (
            "未签收订单数量",
            "未送达订单数量",
            "尚未签收订单数量",
            "undelivered orders",
        ):
            with self.subTest(question=question):
                self.assertEqual(identify_metric(question), "undelivered")

    def test_undelivered_prefers_orders_table(self) -> None:
        self.assertEqual(
            preferred_tables_for_question("未签收订单数量是多少？"), ["orders"]
        )

    def test_undelivered_vague_product_metric_requires_clarification(self) -> None:
        for question in (
            "2016年未签收商品情况",
            "2016年未送达商品情况",
            "2017年尚未签收商品情况",
        ):
            with self.subTest(question=question):
                self.assertTrue(undelivered_metric_is_ambiguous(question))
                state = create_initial_state(question, as_of_date="2018-10-17")
                self.assertEqual(state["request_status"], "clarification_required")

    def test_explicit_status_filter_is_not_rewritten(self) -> None:
        question = "统计2017年 status != 'delivered' 的订单数量"
        self.assertFalse(question_requests_undelivered_scope(question))
        self.assertNotEqual(identify_metric(question), "undelivered")

    def test_delivered_scope_does_not_absorb_undelivered(self) -> None:
        self.assertFalse(question_requests_delivered_scope("not delivered orders"))
        self.assertFalse(question_requests_delivered_scope("undelivered orders"))
        self.assertTrue(question_requests_delivered_scope("已签收订单"))


if __name__ == "__main__":
    unittest.main()
