"""Deterministic parser-robustness tests for the intent layer."""

from __future__ import annotations

import unittest
from typing import Any

from src.contracts import FilterNode
from src.intent import build_structured_intent
from src.state import create_initial_state


def _leaves(node: FilterNode | None) -> list[tuple[str, str | None, Any]]:
    if node is None:
        return []
    if node.children:
        return [leaf for child in node.children for leaf in _leaves(child)]
    return [(node.op, node.field, node.value)]


class TimeExpressionTests(unittest.TestCase):
    def test_two_digit_year(self) -> None:
        self.assertEqual(build_structured_intent("17年订单量最大的五个州").time_range, "2017")
        self.assertEqual(build_structured_intent("18年订单量最大的3个州").time_range, "2018")

    def test_half_and_quarter(self) -> None:
        self.assertEqual(build_structured_intent("2017年上半年订单量").time_range, "2017-H1")
        self.assertEqual(build_structured_intent("2017年前六个月订单量").time_range, "2017-H1")
        self.assertEqual(build_structured_intent("2017年一季度订单量").time_range, "2017-Q1")


class RankingTests(unittest.TestCase):
    def test_chinese_numeral_ranking(self) -> None:
        intent = build_structured_intent("17年订单量最大的五个州")
        self.assertEqual(intent.time_range, "2017")
        self.assertEqual(intent.counting_unit, "order")
        self.assertEqual(intent.group_by, ["customer_state"])
        self.assertEqual(intent.limit, 5)
        self.assertEqual(intent.analysis_type, "ranking")
        self.assertEqual(intent.sort_order, "desc")

    def test_arabic_numeral_ranking(self) -> None:
        intent = build_structured_intent("18年订单量最大的3个州")
        self.assertEqual(intent.time_range, "2018")
        self.assertEqual(intent.limit, 3)
        self.assertEqual(intent.group_by, ["customer_state"])

    def test_which_n_ranking(self) -> None:
        intent = build_structured_intent("17年哪五个客户州下单最多")
        self.assertEqual(intent.time_range, "2017")
        self.assertEqual(intent.limit, 5)
        self.assertEqual(intent.group_by, ["customer_state"])
        self.assertEqual(intent.analysis_type, "ranking")


class UndeliveredSynonymTests(unittest.TestCase):
    def test_colloquial_undelivered(self) -> None:
        for q in ("2017年还有多少订单没送到客户手里", "只看2017年SP州已签收或者RJ州还没签收的订单一共有多少"):
            intent = build_structured_intent(q)
            leaves = _leaves(intent.filters)
            self.assertTrue(any(op == "undelivered" for op, _, _ in leaves), q)


class TrendVsChangeTests(unittest.TestCase):
    def test_monthly_gmv_change_is_trend(self) -> None:
        intent = build_structured_intent("帮我分析2017年的月度GMV变化")
        self.assertEqual(intent.metric, "monthly_delivered_gmv")
        self.assertEqual(intent.analysis_type, "trend")
        self.assertEqual(intent.group_by, ["month"])
        state = create_initial_state("帮我分析2017年的月度GMV变化", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "ready")

    def test_growth_without_baseline_clarifies(self) -> None:
        intent = build_structured_intent("2017年GMV增长了多少")
        self.assertEqual(intent.analysis_type, "change")
        state = create_initial_state("2017年GMV增长了多少", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "clarification_required")


class MetricVsFilterTests(unittest.TestCase):
    def test_value_threshold_is_filter_not_metric(self) -> None:
        intent = build_structured_intent("2017年商品金额超过500的订单有多少")
        self.assertEqual(intent.counting_unit, "order")
        self.assertIn(("gt", "item_value", 500.0), _leaves(intent.filters))
        state = create_initial_state("2017年商品金额超过500的订单有多少", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "ready")

    def test_value_threshold_lower_bound(self) -> None:
        intent = build_structured_intent("2017年商品金额低于300的订单数量")
        self.assertIn(("lt", "item_value", 300.0), _leaves(intent.filters))

    def test_price_filter_is_item_grain(self) -> None:
        intent = build_structured_intent("2017年商品价格超过500的商品有哪些")
        self.assertEqual(intent.counting_unit, "distinct_product")
        self.assertEqual(intent.entity, "product")
        self.assertIn(("gt", "price", 500.0), _leaves(intent.filters))


class NegationTests(unittest.TestCase):
    def test_not_delivered(self) -> None:
        intent = build_structured_intent("2017年订单状态不是 delivered 的数量")
        self.assertIsNone(intent.metric)
        self.assertIn(("ne", "status", "delivered"), _leaves(intent.filters))
        self.assertNotIn(("eq", "status", "delivered"), _leaves(intent.filters))

    def test_not_canceled(self) -> None:
        intent = build_structured_intent("2017年状态不是 canceled 的订单数量")
        self.assertIn(("ne", "status", "canceled"), _leaves(intent.filters))


class LocationAliasTests(unittest.TestCase):
    def test_chinese_state_aliases(self) -> None:
        intent = build_structured_intent("2017年圣保罗和里约的GMV分别是多少")
        self.assertIn(("in", "customer_state", ["SP", "RJ"]), _leaves(intent.filters))


class DistributionTests(unittest.TestCase):
    def test_status_distribution(self) -> None:
        intent = build_structured_intent("2018年订单状态分布怎么样")
        self.assertEqual(intent.group_by, ["status"])
        self.assertEqual(intent.counting_unit, "order")
        self.assertIn(intent.analysis_type, {"composition", "summary"})
        state = create_initial_state("2018年订单状态分布怎么样", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "ready")

    def test_payment_distribution(self) -> None:
        intent = build_structured_intent("2017年支付方式分布怎么样")
        self.assertEqual(intent.group_by, ["payment_type"])
        state = create_initial_state("2017年支付方式分布怎么样", as_of_date="2018-10-17")
        self.assertEqual(state["request_status"], "ready")


class ConservativeSemanticLayerTests(unittest.TestCase):
    def test_parser_uncertainty_is_not_clarification(self) -> None:
        # Parser does not cover seller/customer/payment counting -> legacy fallback,
        # NOT clarification.
        for q in ("卖家最多的州", "客户记录多少条", "支付有多少笔", "评分最高的商品"):
            with self.subTest(question=q):
                intent = build_structured_intent(q)
                self.assertEqual(intent.semantic_coverage, "low")
                self.assertNotIn("metric_unknown", intent.ambiguities)
                state = create_initial_state(q, as_of_date="2018-10-17")
                self.assertNotEqual(state["request_status"], "clarification_required")

    def test_governed_question_is_high_coverage(self) -> None:
        intent = build_structured_intent("2017年未签收订单数量")
        self.assertEqual(intent.semantic_coverage, "high")
        intent2 = build_structured_intent("2017年商品金额超过500的订单有多少")
        self.assertEqual(intent2.semantic_coverage, "high")

    def test_value_grain_is_partial(self) -> None:
        intent = build_structured_intent("2017年商品价格超过500的商品有哪些")
        self.assertEqual(intent.semantic_coverage, "partial")

    def test_low_coverage_resolves_no_table(self) -> None:
        from src.query_plan import resolve_tables
        intent = build_structured_intent("卖家最多的州")
        self.assertEqual(resolve_tables(intent, "卖家最多的州"), [])

    def test_true_ambiguity_still_clarifies(self) -> None:
        for q in ("2017年商品情况怎么样", "哪个地区表现最好", "销售额增长了多少"):
            with self.subTest(question=q):
                state = create_initial_state(q, as_of_date="2018-10-17")
                self.assertEqual(state["request_status"], "clarification_required")


class TimePlanningTests(unittest.TestCase):
    def _plan(self, q):
        from src.query_plan import build_query_plan, resolve_tables
        intent = build_structured_intent(q)
        return build_query_plan(intent, q, resolve_tables(intent, q), {})

    def test_monthly_count_sorts_by_time_asc(self):
        plan = self._plan("按月统计订单数量")
        self.assertEqual(plan.time_grain, "month")
        self.assertEqual(plan.order_by, ["month ASC"])

    def test_year_boundaries(self):
        plan = self._plan("2017年订单数量")
        self.assertEqual(plan.time_boundaries, {"start": "2017-01-01", "end": "2018-01-01"})

    def test_quarter_boundaries(self):
        plan = self._plan("2017年第四季度各月订单数")
        self.assertEqual(plan.time_boundaries, {"start": "2017-10-01", "end": "2018-01-01"})
        self.assertEqual(plan.time_grain, "month")

    def test_aov_metric_expression(self):
        plan = self._plan("已签收订单的平均客单价是多少")
        self.assertEqual(plan.metric_expression, "SUM(item_value) / COUNT(*)")


class ClarificationPrecisionTests(unittest.TestCase):
    def test_still_clarifies_true_ambiguity(self) -> None:
        for q in ("2017年商品情况怎么样", "哪个地区表现最好", "销售额增长了多少"):
            with self.subTest(question=q):
                state = create_initial_state(q, as_of_date="2018-10-17")
                self.assertEqual(state["request_status"], "clarification_required")

    def test_no_false_clarification(self) -> None:
        for q in (
            "2017年月度GMV变化",
            "2018年订单状态分布",
            "17年订单量最大的五个州",
            "SP和RJ哪个GMV更高",
            "商品金额超过500的订单有多少",
        ):
            with self.subTest(question=q):
                state = create_initial_state(q, as_of_date="2018-10-17")
                self.assertNotEqual(state["request_status"], "clarification_required")


if __name__ == "__main__":
    unittest.main()
