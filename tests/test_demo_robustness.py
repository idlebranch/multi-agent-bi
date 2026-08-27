"""Deterministic demo-robustness suite for the semantic upgrade.

Validates the deterministic layers (structured intent, clarification gate,
query plan, semantic consistency, result analysis) without any live LLM or
database. The full end-to-end (SQL generation + execution) is exercised by the
manual/live smoke, not here.
"""

from __future__ import annotations

import unittest
from typing import Any

from src.analysis import analyze_result
from src.contracts import FilterNode, QueryPlan, StructuredIntent
from src.intent import build_structured_intent, clarification_decision
from src.query_plan import build_query_plan, resolve_tables
from src.semantic_rules import check_plan_consistency
from src.state import create_initial_state


def _flatten(node: FilterNode | None) -> list[tuple[str, str | None, Any]]:
    if node is None:
        return []
    if node.children:
        return [("group", node.op, None)] + [
            leaf for child in node.children for leaf in _flatten(child)
        ]
    return [(node.op, node.field, node.value)]


def _plan(question: str) -> QueryPlan:
    intent = build_structured_intent(question)
    tables = resolve_tables(intent, question)
    columns: dict[str, list[str]] = {}
    return build_query_plan(intent, question, tables, columns)


class ParaphraseTests(unittest.TestCase):
    def test_undelivered_paraphrases_share_one_intent(self) -> None:
        questions = (
            "2017年未签收订单数量",
            "2017年未送达订单有多少",
            "2017年尚未签收的订单数量",
        )
        intents = [build_structured_intent(q) for q in questions]
        for intent in intents:
            self.assertEqual(intent.metric, "undelivered")
            self.assertEqual(intent.counting_unit, "order")
            self.assertEqual(intent.time_range, "2017")
            self.assertEqual(resolve_tables(intent, questions[0]), ["orders"])
        self.assertEqual(intents[0].metric, intents[1].metric)
        self.assertEqual(intents[1].metric, intents[2].metric)


class ComplexFilterTests(unittest.TestCase):
    def test_state_status_filter(self) -> None:
        intent = build_structured_intent("2017年SP州已签收订单数量")
        leaves = _flatten(intent.filters)
        self.assertIn(("in", "customer_state", ["SP"]), leaves)
        self.assertIn(("eq", "status", "delivered"), leaves)
        self.assertEqual(resolve_tables(intent, "2017年SP州已签收订单数量"), ["order_financials"])

    def test_multi_state_in_filter(self) -> None:
        intent = build_structured_intent("2017年SP和RJ州已签收订单数量")
        self.assertIn(("in", "customer_state", ["SP", "RJ"]), _flatten(intent.filters))

    def test_exclude_canceled_filter(self) -> None:
        intent = build_structured_intent("2017年SP州除取消订单外的订单数量")
        self.assertIn(("ne", "status", "canceled"), _flatten(intent.filters))

    def test_value_threshold_filter(self) -> None:
        intent = build_structured_intent("2017年SP和RJ州中商品金额超过500的订单数量")
        leaves = _flatten(intent.filters)
        self.assertIn(("in", "customer_state", ["SP", "RJ"]), leaves)
        self.assertIn(("gt", "item_value", 500.0), leaves)


class LogicalCombinationTests(unittest.TestCase):
    def test_or_of_two_and_groups(self) -> None:
        intent = build_structured_intent("2017年SP州已签收或者RJ州未签收的订单数量")
        self.assertIsNotNone(intent.filters)
        self.assertEqual(intent.filters.op, "or")
        self.assertEqual(len(intent.filters.children), 2)
        self.assertTrue(all(child.op == "and" for child in intent.filters.children))


class TimeTests(unittest.TestCase):
    def test_monthly_group_by(self) -> None:
        intent = build_structured_intent("2017年每个月订单数量")
        self.assertEqual(intent.group_by, ["month"])
        self.assertEqual(intent.time_range, "2017")

    def test_quarter_time_range(self) -> None:
        self.assertEqual(build_structured_intent("2017年Q1订单数量").time_range, "2017-Q1")

    def test_half_year_time_range(self) -> None:
        self.assertEqual(build_structured_intent("2017年上半年订单数量").time_range, "2017-H1")
        self.assertEqual(build_structured_intent("2017年下半年订单数量").time_range, "2017-H2")


class RankingTests(unittest.TestCase):
    def test_top_states(self) -> None:
        intent = build_structured_intent("2017年订单最多的5个客户州")
        self.assertEqual(intent.analysis_type, "ranking")
        self.assertEqual(intent.counting_unit, "order")
        self.assertEqual(intent.group_by, ["customer_state"])
        self.assertEqual(intent.limit, 5)
        self.assertEqual(intent.sort_order, "desc")
        state = create_initial_state("2017年订单最多的5个客户州", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "ready")


class TrendTests(unittest.TestCase):
    def test_monthly_gmv_trend(self) -> None:
        intent = build_structured_intent("2017年按月GMV趋势怎么样")
        self.assertEqual(intent.metric, "monthly_delivered_gmv")
        self.assertEqual(intent.analysis_type, "trend")
        self.assertEqual(intent.group_by, ["month"])


class ComparisonTests(unittest.TestCase):
    def test_state_gmv_comparison(self) -> None:
        intent = build_structured_intent("2017年SP和RJ的GMV相差多少")
        self.assertEqual(intent.analysis_type, "comparison")
        self.assertEqual(intent.counting_unit, "value")
        self.assertIn(("in", "customer_state", ["SP", "RJ"]), _flatten(intent.filters))
        self.assertEqual(intent.group_by, ["customer_state"])


class ClarificationTests(unittest.TestCase):
    def test_vague_metric_clarifies(self) -> None:
        state = create_initial_state("2017年商品情况", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "clarification_required")

    def test_ambiguous_ranking_clarifies(self) -> None:
        state = create_initial_state("哪个地区表现最好", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "clarification_required")

    def test_change_without_baseline_clarifies(self) -> None:
        state = create_initial_state("销售额增长了多少", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "clarification_required")


class OutOfScopeAndSafetyTests(unittest.TestCase):
    def test_out_of_range_year_is_out_of_scope(self) -> None:
        state = create_initial_state("2015年订单情况", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "out_of_scope")

    def test_write_request_is_rejected(self) -> None:
        state = create_initial_state("删除所有订单后告诉我还有多少条", as_of_date="2018-10-17")
        self.assertEqual(state["input_guard_status"], "rejected")
        self.assertEqual(state["request_status"], "rejected")


class SemanticConsistencyTests(unittest.TestCase):
    def test_dropped_state_filter_is_rejected(self) -> None:
        plan = _plan("2017年SP和RJ州已签收订单数量")
        bad_sql = "SELECT COUNT(*) FROM order_financials WHERE status = 'delivered'"
        issues = check_plan_consistency(plan, bad_sql)
        codes = {issue.code for issue in issues}
        self.assertIn("missing_status_filter", codes)

    def test_complete_sql_passes_consistency(self) -> None:
        plan = _plan("2017年SP和RJ州已签收订单数量")
        good_sql = (
            "SELECT COUNT(*) FROM order_financials "
            "WHERE customer_state IN ('SP','RJ') AND status = 'delivered'"
        )
        self.assertEqual(check_plan_consistency(plan, good_sql), [])


class ResultAnalysisTests(unittest.TestCase):
    def test_trend_facts(self) -> None:
        rows = [
            {"month": "2017-01", "gmv": 100},
            {"month": "2017-02", "gmv": 120},
            {"month": "2017-03", "gmv": 90},
        ]
        analysis = analyze_result(rows, "trend")
        self.assertEqual(analysis.analysis_type, "trend")
        self.assertTrue(analysis.facts)
        joined = " ".join(analysis.facts)
        self.assertIn("2017-02", joined)

    def test_composition_facts(self) -> None:
        rows = [
            {"payment_type": "credit_card", "value": 70},
            {"payment_type": "boleto", "value": 30},
        ]
        analysis = analyze_result(rows, "composition")
        self.assertTrue(any("70" in fact for fact in analysis.facts))

    def test_comparison_facts(self) -> None:
        rows = [{"state": "SP", "gmv": 200}, {"state": "RJ", "gmv": 100}]
        analysis = analyze_result(rows, "comparison")
        self.assertEqual(analysis.analysis_type, "comparison")
        self.assertTrue(any("差值" in fact for fact in analysis.facts))

    def test_ranking_facts(self) -> None:
        rows = [{"state": "SP", "n": 5}, {"state": "RJ", "n": 3}, {"state": "MG", "n": 2}]
        analysis = analyze_result(rows, "ranking")
        self.assertTrue(any("SP" in fact for fact in analysis.facts))


class RepeatedStabilityTests(unittest.TestCase):
    def test_intent_and_plan_are_deterministic(self) -> None:
        questions = (
            "2017年未签收订单数量",
            "2017年SP和RJ州已签收订单数量",
            "2017年SP州已签收或者RJ州未签收的订单数量",
            "2017年订单最多的5个客户州",
            "2017年按月GMV趋势怎么样",
        )
        for question in questions:
            snapshots = set()
            for _ in range(3):
                intent = build_structured_intent(question)
                plan = build_query_plan(
                    intent, question, resolve_tables(intent, question), {}
                )
                snapshots.add((intent.model_dump_json(), plan.model_dump_json()))
            self.assertEqual(len(snapshots), 1, question)


class StructuredIntentContractTests(unittest.TestCase):
    def test_intent_is_stored_in_initial_state(self) -> None:
        state = create_initial_state("2017年未签收订单数量", as_of_date="2018-10-17")
        self.assertIn("structured_intent", state)
        self.assertIsInstance(state["structured_intent"], dict)
        intent = StructuredIntent.model_validate(state["structured_intent"])
        self.assertEqual(intent.metric, "undelivered")

    def test_clarification_decision_matches_intent(self) -> None:
        intent = build_structured_intent("销售额增长了多少")
        decision = clarification_decision(intent)
        self.assertTrue(decision["needs_clarification"])
        self.assertTrue(decision["options"])


if __name__ == "__main__":
    unittest.main()
