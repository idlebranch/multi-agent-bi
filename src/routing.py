"""Pure routing policy shared by stable and experimental supervisors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.config import DEFAULT_MAX_ITERATIONS
from src.policy import policy_limit
from src.state import BIAgentState


NextNode = Literal[
    "schema_linking",
    "sql_generation",
    "sql_review",
    "sql_validation",
    "sql_execution",
    "format_answer",
]


@dataclass(frozen=True)
class RouteDecision:
    next_node: NextNode
    reason: str


def decide_next_node(state: BIAgentState) -> RouteDecision:
    """Choose the next stage from explicit statuses, not truthiness."""
    iteration = state.get("iteration", 0)
    max_iterations = min(
        state.get("max_iterations", DEFAULT_MAX_ITERATIONS),
        int(policy_limit("workflow_iterations", 12)),
    )
    visits = state.get("visit_count", {})
    schema_attempts = int(policy_limit("schema_attempts", 2))
    sql_attempts = int(policy_limit("sql_attempts", 3))

    if state.get("input_guard_status") == "rejected":
        return RouteDecision(
            "format_answer",
            "input guard rejected a write, injection, rule-bypass, or secret request",
        )

    request_status = state.get("request_status", "ready")
    if request_status in {"clarification_required", "out_of_scope"}:
        return RouteDecision(
            "format_answer",
            f"request classified as {request_status} before schema linking",
        )

    if iteration >= max_iterations:
        return RouteDecision(
            "format_answer",
            f"reached the workflow limit ({max_iterations})",
        )

    schema_status = state.get("schema_status", "not_started")
    if schema_status == "no_match":
        return RouteDecision("format_answer", "the catalog has no matching business data")
    if schema_status == "failed":
        if visits.get("schema_linking", 0) < schema_attempts:
            return RouteDecision("schema_linking", "retry catalog selection once")
        return RouteDecision("format_answer", "catalog selection failed twice")
    if schema_status != "succeeded":
        return RouteDecision("schema_linking", "schema has not been selected")

    sql_status = state.get("sql_status", "not_started")
    if sql_status == "failed":
        if visits.get("sql_generation", 0) < sql_attempts:
            return RouteDecision("sql_generation", "retry SQL generation")
        return RouteDecision("format_answer", "SQL generation failed repeatedly")
    if sql_status != "succeeded" or not state.get("sql"):
        return RouteDecision("sql_generation", "SQL has not been generated")

    execution_status = state.get("execution_status", "not_started")
    if execution_status == "failed":
        if state.get("execution_error_code") in {"query_timeout", "queue_timeout"}:
            return RouteDecision(
                "format_answer",
                "database capacity limit reached; do not rewrite semantically valid SQL",
            )
        if visits.get("sql_generation", 0) < sql_attempts:
            return RouteDecision("sql_generation", "rewrite SQL after execution failure")
        return RouteDecision("format_answer", "SQL execution failed repeatedly")

    validation_status = state.get("validation_status", "not_started")
    if validation_status == "failed":
        if visits.get("sql_generation", 0) < sql_attempts:
            return RouteDecision("sql_generation", "rewrite SQL after validation failure")
        return RouteDecision("format_answer", "SQL validation failed repeatedly")

    review_status = state.get("review_status", "not_started")
    if review_status == "failed":
        issue_codes = {
            str(issue.get("code", ""))
            for issue in state.get("review_issues", [])
            if isinstance(issue, dict)
        }
        if (
            issue_codes.intersection({"unanswerable", "wrong_columns"})
            and visits.get("schema_linking", 0) < schema_attempts
        ):
            return RouteDecision(
                "schema_linking",
                "review found missing schema dimensions; re-run catalog selection",
            )
        if visits.get("sql_generation", 0) < sql_attempts:
            return RouteDecision("sql_generation", "rewrite SQL after reviewer feedback")
        return RouteDecision("format_answer", "SQL review failed repeatedly")
    if review_status != "succeeded":
        return RouteDecision("sql_review", "SQL needs independent semantic review")

    if validation_status != "succeeded" or not state.get("sql_validated", False):
        return RouteDecision("sql_validation", "generated SQL has not been validated")

    if execution_status == "not_started":
        return RouteDecision("sql_execution", "validated SQL has not been executed")

    # A successful zero-row query arrives here and terminates normally.
    return RouteDecision("format_answer", "query execution completed")
