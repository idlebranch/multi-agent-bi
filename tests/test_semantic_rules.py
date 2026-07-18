from __future__ import annotations

import unittest

from src.contracts import ReviewIssue
from src.semantic_rules import (
    identify_metric,
    preferred_tables_for_question,
    reconcile_llm_issues,
    review_sql_semantics,
)


class SemanticRuleTests(unittest.TestCase):
    def test_identifies_governed_metrics_and_preferred_views(self) -> None:
        cases = {
            "各订单状态有多少订单？": "orders",
            "每月已签收商品 GMV 是多少？": "product_sales",
            "按月计算订单取消率。": "orders",
            "已签收 GMV 最高的五个商品类别是什么？": "category_sales_summary",
            "按商品类别计算已签收订单的平均客单价。": "category_sales_summary",
            "已签收订单的平均客单价是多少？": "order_financials",
            "已签收订单的按时送达率是多少？": "delivery_kpis",
            "各支付方式的支付金额是多少？": "payment_type_summary",
            "下过两次及以上订单的消费者有多少？": "customer_order_summary",
            "已签收 GMV 最高的五个卖家州是什么？": "product_sales",
        }
        for question, table in cases.items():
            with self.subTest(question=question):
                self.assertIsNotNone(identify_metric(question))
                self.assertEqual(preferred_tables_for_question(question), [table])

    def test_rejects_category_translation_join_against_english_view(self) -> None:
        issues = review_sql_semantics(
            "已签收 GMV 最高的五个商品类别是什么？",
            "SELECT ct.category_name_english, SUM(ps.price) FROM product_sales ps "
            "JOIN category_translations ct ON ps.category_name = ct.category_name "
            "WHERE ps.order_status = 'delivered' GROUP BY 1",
        )
        self.assertIn("join_fanout", [issue.code for issue in issues])

    def test_rejects_on_time_fraction_without_percentage_scaling(self) -> None:
        issues = review_sql_semantics(
            "已签收订单的按时送达率是多少？",
            "SELECT AVG(delivered_on_time) FROM order_delivery_metrics",
        )
        self.assertIn("wrong_metric", [issue.code for issue in issues])

    def test_rejects_cancellation_rate_returned_as_fraction(self) -> None:
        issues = review_sql_semantics(
            "按月计算订单取消率。",
            "SELECT strftime('%Y-%m', purchase_timestamp) AS month, "
            "SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END) * 1.0 / "
            "COUNT(*) AS cancellation_rate FROM orders GROUP BY 1",
        )
        self.assertIn("wrong_metric", [issue.code for issue in issues])

    def test_rejects_unrequested_payment_status_filter(self) -> None:
        issues = review_sql_semantics(
            "各支付方式的支付金额是多少？",
            "SELECT payment_type, SUM(payment_value) FROM payments p JOIN orders o "
            "ON p.order_id = o.order_id WHERE o.status = 'delivered' GROUP BY 1",
        )
        self.assertIn("wrong_metric", [issue.code for issue in issues])

    def test_accepts_repeat_customer_summary_count(self) -> None:
        issues = review_sql_semantics(
            "下过两次及以上订单的消费者有多少？",
            "SELECT COUNT(*) AS repeat_customers FROM customer_order_summary "
            "WHERE order_count >= 2",
        )
        self.assertEqual(issues, [])

    def test_rejects_time_breakdown_from_timeless_summary(self) -> None:
        issues = review_sql_semantics(
            "分别找出 2017 年和 2018 年已签收 GMV 最高的商品类别。",
            "SELECT category_name, delivered_gmv FROM category_sales_summary",
        )
        self.assertIn("wrong_columns", [issue.code for issue in issues])

    def test_customer_summary_can_serve_delivered_gmv_percentiles(self) -> None:
        issues = review_sql_semantics(
            "所有至少有一笔已签收订单的消费者中，累计已签收 GMV 的第 99 百分位门槛是多少？",
            "WITH ranked AS (SELECT delivered_gmv, CUME_DIST() OVER "
            "(ORDER BY delivered_gmv) AS p FROM customer_order_summary "
            "WHERE delivered_order_count >= 1) "
            "SELECT MIN(delivered_gmv) FROM ranked WHERE p >= 0.99",
        )
        self.assertEqual(issues, [])

    def test_category_average_uses_precomputed_distinct_order_count(self) -> None:
        issues = review_sql_semantics(
            "按商品类别计算已签收订单的平均客单价。",
            "SELECT category_name, delivered_gmv / CAST(order_count AS REAL) AS aov "
            "FROM category_sales_summary",
        )
        self.assertEqual(issues, [])

    def test_rejects_window_filter_at_same_select_level(self) -> None:
        issues = review_sql_semantics(
            "按月统计已签收商品 GMV 并给出环比，只返回 2017-02。",
            "WITH monthly AS (SELECT month, SUM(price) AS gmv FROM sales GROUP BY month) "
            "SELECT month, LAG(gmv) OVER (ORDER BY month) FROM monthly "
            "WHERE month = '2017-02'",
        )
        self.assertIn("wrong_date_range", [issue.code for issue in issues])

    def test_accepts_explicit_current_and_previous_month_ctes(self) -> None:
        question = "按月统计已签收商品 GMV 并给出环比，只返回 2017-02。"
        sql = (
            "WITH current_month AS (SELECT SUM(price) AS delivered_gmv "
            "FROM product_sales WHERE purchase_timestamp >= '2017-02-01' "
            "AND purchase_timestamp < '2017-03-01'), previous_month AS ("
            "SELECT SUM(price) AS prev_gmv FROM product_sales "
            "WHERE purchase_timestamp >= '2017-01-01' "
            "AND purchase_timestamp < '2017-02-01') "
            "SELECT (delivered_gmv - prev_gmv) / prev_gmv FROM current_month "
            "CROSS JOIN previous_month"
        )
        issues = reconcile_llm_issues(
            question,
            sql,
            [
                ReviewIssue(
                    code="wrong_date_range",
                    severity="high",
                    message="The main CTE contains only February.",
                )
            ],
        )
        self.assertEqual(issues, [])

    def test_rejects_unknown_delivery_outcomes_classified_as_late(self) -> None:
        issues = review_sql_semantics(
            "比较按时送达与超时送达订单的平均评价分和订单数。",
            "SELECT CASE WHEN delivered_on_time = 1 THEN 'on_time' ELSE 'late' END, "
            "AVG(review_score) FROM order_delivery_metrics JOIN reviews USING(order_id) "
            "GROUP BY 1",
        )
        self.assertIn("wrong_metric", [issue.code for issue in issues])

    def test_drops_hallucinated_status_and_date_findings(self) -> None:
        issues = [
            ReviewIssue(
                code="missing_status_filter",
                severity="high",
                message="A delivered status filter is required.",
            ),
            ReviewIssue(
                code="wrong_date_range",
                severity="high",
                message="The query must be limited to last month.",
            ),
        ]
        reconciled = reconcile_llm_issues(
            "已签收订单的按时送达率是多少？",
            "SELECT on_time_delivery_pct FROM delivery_kpis",
            issues,
        )
        self.assertEqual(reconciled, [])


if __name__ == "__main__":
    unittest.main()
