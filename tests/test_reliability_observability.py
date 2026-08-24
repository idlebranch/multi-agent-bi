from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.run_benchmark import compare_case_results
from src.numerical_faithfulness import enforce_numerical_faithfulness
from src.nodes.format_answer import format_answer_node
from src.observability import (
    extract_token_usage,
    serialize_safe_run_log,
    summarize_llm_observations,
)
from src.state import create_initial_state


class ReliabilityObservabilityTests(unittest.TestCase):
    def test_request_and_run_ids_are_distinct_and_present(self) -> None:
        state = create_initial_state("订单数", as_of_date="2018-10-17")
        self.assertTrue(state["request_id"])
        self.assertTrue(state["run_id"])
        self.assertNotEqual(state["request_id"], state["run_id"])

    def test_provider_reported_token_usage_is_used_without_estimation(self) -> None:
        response = SimpleNamespace(
            usage_metadata={
                "input_tokens": 101,
                "output_tokens": 29,
                "total_tokens": 130,
            },
            response_metadata={},
        )
        self.assertEqual(
            extract_token_usage(response),
            {"prompt_tokens": 101, "completion_tokens": 29, "total_tokens": 130},
        )
        self.assertIsNone(extract_token_usage(SimpleNamespace()))

    def test_llm_metrics_are_stage_calls_not_provider_http_requests(self) -> None:
        metrics = summarize_llm_observations(
            [
                {
                    "stage": "sql_generation",
                    "status": "succeeded",
                    "duration_ms": 1.0,
                    "token_usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                },
                {
                    "stage": "sql_generation",
                    "status": "succeeded",
                    "duration_ms": 1.0,
                    "token_usage": None,
                },
            ]
        )
        self.assertEqual(metrics["llm_stage_calls"], 2)
        self.assertEqual(metrics["sql_repair_llm_calls"], 1)
        self.assertIsNone(metrics["provider_request_count"])
        self.assertEqual(metrics["token_usage_availability"], "partial")

    def test_structured_log_excludes_prompt_sql_result_and_secret_errors(self) -> None:
        state = create_initial_state(
            "show api_key=prompt-secret", as_of_date="2018-10-17"
        )
        state.update(
            {
                "sql": "SELECT 'sql-secret' AS leaked_value",
                "sql_result": [{"leaked_value": "result-secret"}],
                "error_history": [
                    {"source": "provider", "message": "bearer provider-secret"}
                ],
                "response_status": "failed",
            }
        )
        serialized = serialize_safe_run_log(state)
        for forbidden in (
            "prompt-secret",
            "sql-secret",
            "result-secret",
            "provider-secret",
            "SELECT",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertIn(state["request_id"], serialized)
        self.assertIn(state["run_id"], serialized)
        self.assertIn("question_sha256", serialized)
        self.assertIn("sql_sha256", serialized)

    def test_percentage_scale_conflict_uses_raw_result(self) -> None:
        rows = [
            {"month": "2018-01", "cancellation_rate": Decimal("0.4677")},
            {"month": "2018-02", "cancellation_rate": Decimal("1.0850")},
        ]
        answer, metadata = enforce_numerical_faithfulness(
            "2018年1月为46.77%，2月为108.50%。", rows
        )
        self.assertEqual(metadata["status"], "corrected")
        self.assertIn("0.4677", answer)
        self.assertIn("1.0850", answer)
        self.assertNotIn("46.77%", answer)

    def test_matching_percentage_claim_is_preserved(self) -> None:
        original = "按时送达率为97.25%。"
        answer, metadata = enforce_numerical_faithfulness(
            original, [{"on_time_delivery_pct": Decimal("97.25")}]
        )
        self.assertEqual(metadata["status"], "passed")
        self.assertEqual(answer, original)

    def test_answer_node_records_usage_and_corrects_percentage_scaling(self) -> None:
        state = create_initial_state("取消率", as_of_date="2018-10-17")
        state.update(
            {
                "execution_status": "succeeded",
                "result_row_count": 1,
                "sql_result": [
                    {"month": "2018-01", "cancellation_rate": Decimal("0.4677")}
                ],
            }
        )
        response = SimpleNamespace(
            content="2018年1月取消率为46.77%。",
            usage_metadata={
                "input_tokens": 20,
                "output_tokens": 8,
                "total_tokens": 28,
            },
            response_metadata={},
        )
        with patch("src.nodes.format_answer.get_llm") as mocked_llm:
            mocked_llm.return_value.invoke.return_value = response
            result = format_answer_node(state)
        self.assertEqual(result["numerical_faithfulness"]["status"], "corrected")
        self.assertEqual(result["llm_stage_calls"][0]["stage"], "format_answer")
        self.assertEqual(
            result["llm_stage_calls"][0]["token_usage"]["total_tokens"], 28
        )

    def test_quarter_evaluator_accepts_two_and_three_column_shapes(self) -> None:
        case = {
            "comparison_gold_transform": "split_year_quarter",
            "numeric_tolerance": 0.02,
            "ordering_required": True,
        }
        gold = [{"quarter": "2018-Q2", "order_count": 19979}]
        variants = (
            [{"year": 2018, "quarter": 2, "order_count": 19979}],
            [{"year": "2018", "quarter": "Q2", "order_count": 19979}],
            [{"quarter": "2018-Q2", "order_count": 19979}],
        )
        for rows in variants:
            with self.subTest(rows=rows):
                self.assertTrue(compare_case_results(case, gold, rows)["passed"])


if __name__ == "__main__":
    unittest.main()
