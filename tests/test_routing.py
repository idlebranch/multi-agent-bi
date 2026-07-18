from __future__ import annotations

import unittest

from src.routing import decide_next_node
from src.state import create_initial_state


class RoutingTests(unittest.TestCase):
    def test_new_request_starts_with_catalog_selection(self) -> None:
        state = create_initial_state("question", as_of_date="2026-07-17")
        state["iteration"] = 1
        self.assertEqual(decide_next_node(state).next_node, "schema_linking")

    def test_successful_empty_query_finishes(self) -> None:
        state = create_initial_state("question", as_of_date="2026-07-17")
        state.update(
            {
                "iteration": 5,
                "schema_status": "succeeded",
                "relevant_tables": ["orders"],
                "sql_status": "succeeded",
                "sql": "SELECT * FROM orders WHERE 0",
                "review_status": "succeeded",
                "validation_status": "succeeded",
                "sql_validated": True,
                "execution_status": "succeeded",
                "sql_result": [],
                "result_row_count": 0,
            }
        )
        self.assertEqual(decide_next_node(state).next_node, "format_answer")

    def test_execution_failure_rewrites_sql(self) -> None:
        state = create_initial_state("question", as_of_date="2026-07-17")
        state.update(
            {
                "iteration": 5,
                "schema_status": "succeeded",
                "relevant_tables": ["orders"],
                "sql_status": "succeeded",
                "sql": "SELECT bad FROM orders",
                "review_status": "succeeded",
                "validation_status": "succeeded",
                "sql_validated": True,
                "execution_status": "failed",
                "visit_count": {"sql_generation": 1},
            }
        )
        self.assertEqual(decide_next_node(state).next_node, "sql_generation")

    def test_capacity_timeout_does_not_waste_a_writer_retry(self) -> None:
        state = create_initial_state("question", as_of_date="2026-07-17")
        state.update(
            {
                "iteration": 5,
                "schema_status": "succeeded",
                "sql_status": "succeeded",
                "sql": "SELECT COUNT(*) FROM orders",
                "review_status": "succeeded",
                "validation_status": "succeeded",
                "sql_validated": True,
                "execution_status": "failed",
                "execution_error_code": "query_timeout",
                "visit_count": {"sql_generation": 1},
            }
        )
        self.assertEqual(decide_next_node(state).next_node, "format_answer")

    def test_missing_dimensions_return_to_catalog_once(self) -> None:
        state = create_initial_state("question", as_of_date="2026-07-17")
        state.update(
            {
                "iteration": 4,
                "schema_status": "succeeded",
                "sql_status": "succeeded",
                "sql": "SELECT seller_state FROM product_sales",
                "review_status": "failed",
                "review_issues": [
                    {
                        "code": "wrong_columns",
                        "severity": "high",
                        "message": "customer_state is missing",
                    }
                ],
                "visit_count": {"schema_linking": 1, "sql_generation": 1},
            }
        )
        self.assertEqual(decide_next_node(state).next_node, "schema_linking")

    def test_iteration_limit_stops_at_the_configured_boundary(self) -> None:
        state = create_initial_state("question", max_iterations=12, as_of_date="2026-07-17")
        state["iteration"] = 12
        self.assertEqual(decide_next_node(state).next_node, "format_answer")

    def test_retry_budget_terminates(self) -> None:
        state = create_initial_state("question", as_of_date="2026-07-17")
        state.update(
            {
                "iteration": 8,
                "schema_status": "succeeded",
                "relevant_tables": ["orders"],
                "sql_status": "succeeded",
                "sql": "SELECT bad FROM orders",
                "review_status": "succeeded",
                "validation_status": "failed",
                "visit_count": {"sql_generation": 3},
            }
        )
        self.assertEqual(decide_next_node(state).next_node, "format_answer")

    def test_generated_sql_is_sent_to_independent_review(self) -> None:
        state = create_initial_state("question", as_of_date="2026-07-17")
        state.update(
            {
                "iteration": 3,
                "schema_status": "succeeded",
                "relevant_tables": ["orders"],
                "sql_status": "succeeded",
                "sql": "SELECT COUNT(*) FROM orders",
            }
        )
        self.assertEqual(decide_next_node(state).next_node, "sql_review")


if __name__ == "__main__":
    unittest.main()
