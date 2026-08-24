from __future__ import annotations

import unittest

from benchmarks.evaluators.answer import evaluate_answer
from benchmarks.evaluators.execution import compare_results, compare_top_k_with_boundary_ties
from benchmarks.run_benchmark import _format_rate


class ExecutionEvaluatorRegressionTests(unittest.TestCase):
    def test_different_sql_text_same_result_passes(self) -> None:
        gold = [{"total": 3, "label": "orders"}]
        agent = [{"name": "orders", "count_value": 3}]
        self.assertTrue(compare_results(gold, agent)["passed"])

    def test_transport_success_but_wrong_result_fails(self) -> None:
        self.assertFalse(compare_results([{"count": 3}], [{"count": 4}])["passed"])

    def test_successful_sql_with_wrong_number_fails(self) -> None:
        self.assertFalse(compare_results([{"gmv": 100.0}], [{"gmv": 90.0}])["passed"])

    def test_unordered_rows_pass(self) -> None:
        gold = [{"state": "SP", "count": 2}, {"state": "RJ", "count": 1}]
        agent = [{"n": 1, "s": "RJ"}, {"n": 2, "s": "SP"}]
        self.assertTrue(compare_results(gold, agent, ordered=False)["passed"])

    def test_ordered_rows_fail_when_reversed(self) -> None:
        gold = [{"state": "SP"}, {"state": "RJ"}]
        agent = [{"state": "RJ"}, {"state": "SP"}]
        self.assertFalse(compare_results(gold, agent, ordered=True)["passed"])

    def test_duplicate_count_mismatch_fails(self) -> None:
        gold = [{"value": "A"}, {"value": "A"}, {"value": "B"}]
        agent = [{"value": "A"}, {"value": "B"}, {"value": "B"}]
        self.assertFalse(compare_results(gold, agent)["passed"])

    def test_float_tolerance_is_respected(self) -> None:
        gold = [{"rate": 91.8876}]
        close = [{"rate": 91.88761}]
        far = [{"rate": 91.9}]
        self.assertTrue(compare_results(gold, close, abs_tol=0.001)["passed"])
        self.assertFalse(compare_results(gold, far, abs_tol=0.001)["passed"])

    def test_correct_empty_result_passes(self) -> None:
        self.assertTrue(compare_results([], [])["passed"])

    def test_incorrect_empty_result_fails(self) -> None:
        self.assertFalse(compare_results([], [{"value": 1}])["passed"])

    def test_null_and_date_representations(self) -> None:
        gold = [{"date": "2018-01-01", "value": None}]
        agent = [{"v": None, "d": "2018-01-01 00:00:00"}]
        self.assertTrue(compare_results(gold, agent)["passed"])

    def test_agent_extra_supporting_column_can_be_allowed(self) -> None:
        gold = [{"month": "2017-02", "growth": 109.51}]
        agent = [{"month": "2017-02", "previous": 111798.36, "growth": 109.505}]
        self.assertTrue(
            compare_results(
                gold,
                agent,
                abs_tol=0.02,
                allow_agent_extra_columns=True,
            )["passed"]
        )

    def test_top_k_boundary_ties_allow_interchangeable_entities(self) -> None:
        gold = [
            {"product": "A", "score": 20},
            {"product": "B", "score": 18},
            {"product": "C", "score": 17},
        ]
        agent = [
            {"product": "A", "score": 20},
            {"product": "B", "score": 18},
            {"product": "D", "score": 17},
        ]
        self.assertTrue(
            compare_top_k_with_boundary_ties(
                gold,
                agent,
                metric_column="score",
                entity_columns=["product"],
            )["passed"]
        )


class AnswerEvaluatorTests(unittest.TestCase):
    def test_required_gold_number_and_entity(self) -> None:
        assertions = {
            "required_gold_values": [{"row": 0, "column": "gmv", "tolerance": 0.02}],
            "required_gold_entities": [{"row": 0, "column": "category"}],
        }
        result = evaluate_answer(
            "health_beauty 类别 GMV 为 1,233,131.72。",
            assertions,
            gold_rows=[{"category": "health_beauty", "gmv": 1233131.72}],
            response_status="success",
        )
        self.assertTrue(result["passed"])

    def test_wrong_answer_fails_even_with_success_status(self) -> None:
        result = evaluate_answer(
            "查询成功，GMV 为 10。",
            {
                "expected_status": "success",
                "required_gold_values": [{"row": 0, "column": "gmv"}],
            },
            gold_rows=[{"gmv": 20}],
            response_status="success",
        )
        self.assertFalse(result["passed"])

    def test_chinese_text_adjacent_to_formatted_numbers(self) -> None:
        result = evaluate_answer(
            "复购人数为2,997人，总数为96,096人，占比3.12%。",
            {
                "required_gold_values": [
                    {"row": 0, "column": "repeat", "tolerance": 0.02},
                    {"row": 0, "column": "total", "tolerance": 0.02},
                    {"row": 0, "column": "rate", "tolerance": 0.02},
                ]
            },
            gold_rows=[{"repeat": 2997, "total": 96096, "rate": 3.1188}],
        )
        self.assertTrue(result["passed"])

    def test_chinese_dates_units_and_governed_aliases(self) -> None:
        result = evaluate_answer(
            "2018年1月，圣保罗约506.76万；按时送达。",
            {
                "required_gold_entities": [
                    {"row": 0, "column": "month"},
                    {"row": 0, "column": "city"},
                    {"row": 0, "column": "delivery_group"},
                ],
                "required_gold_values": [
                    {"row": 0, "column": "gmv", "tolerance": 0.02}
                ],
            },
            gold_rows=[
                {
                    "month": "2018-01",
                    "city": "sao paulo",
                    "delivery_group": "on_time",
                    "gmv": 5067633.16,
                }
            ],
        )
        self.assertTrue(result["passed"])

    def test_reviewer_rejection_rate_formats_without_pass_fields(self) -> None:
        self.assertEqual(
            _format_rate({"rejections": 1, "attempts": 4, "rate": 0.25}),
            "25.00% (1/4)",
        )


if __name__ == "__main__":
    unittest.main()
