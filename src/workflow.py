"""Reusable workflow helpers."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any

from src.policy import (
    PolicyViolation,
    node_output_decision,
    project_state_for_agent,
    validate_agent_update,
)
from src.state import BIAgentState, record_error


_NODE_STATUS_FIELDS = {
    "schema_linking": "schema_status",
    "sql_generation": "sql_status",
    "sql_review": "review_status",
    "sql_validation": "validation_status",
    "sql_execution": "execution_status",
    "format_answer": "response_status",
}


def _policy_failure_update(state: BIAgentState, node_name: str, message: str) -> dict:
    if node_name == "format_answer":
        return {
            "final_answer": "请求因内部策略校验失败而终止。",
            "response_status": "failed",
        }

    update_by_node = {
        "schema_linking": {
            "relevant_tables": [],
            "relevant_columns": {},
            "schema_status": "failed",
        },
        "sql_generation": {"sql_status": "failed"},
        "sql_review": {
            "review_status": "failed",
            "review_feedback": message,
            "review_issues": [],
        },
        "sql_validation": {
            "sql_validated": False,
            "validation_status": "failed",
        },
        "sql_execution": {
            "sql_result": [],
            "result_row_count": 0,
            "result_truncated": False,
            "execution_status": "failed",
            "execution_error_code": "policy_violation",
        },
    }
    return {**update_by_node.get(node_name, {}), **record_error(state, node_name, message)}


def with_visit_tracking(
    node_func: Callable[[BIAgentState], dict],
    node_name: str,
) -> Callable[[BIAgentState], dict]:
    """Run one agent with least-context input, output policy, and audit tracking."""
    def wrapped(state: BIAgentState) -> dict:
        started = perf_counter()
        try:
            agent_state = project_state_for_agent(node_name, state)
            result = dict(node_func(agent_state))
            validate_agent_update(node_name, result)
            policy_decision = node_output_decision(
                node_name,
                allowed=True,
                reason="agent state update matches the policy contract",
            )
        except (PolicyViolation, ValueError, TypeError) as exc:
            message = f"policy guard rejected {node_name}: {exc}"
            result = _policy_failure_update(state, node_name, message)
            policy_decision = node_output_decision(
                node_name,
                allowed=False,
                reason=message,
            )

        duration_ms = round((perf_counter() - started) * 1000, 3)
        visits = dict(state.get("visit_count", {}))
        visits[node_name] = visits.get(node_name, 0) + 1
        result["visit_count"] = visits
        result["current_agent"] = node_name
        decisions = list(state.get("policy_decisions", []))
        decisions.append(policy_decision.model_dump(mode="json"))
        result["policy_decisions"] = decisions
        status_field = _NODE_STATUS_FIELDS.get(node_name)
        timing = {
            "node": node_name,
            "attempt": visits[node_name],
            "duration_ms": duration_ms,
            "status": str(result.get(status_field, "succeeded")),
        }
        timings = list(state.get("node_timings", []))
        timings.append(timing)
        result["node_timings"] = timings
        result["last_node_timing"] = timing
        return result

    wrapped.__name__ = getattr(node_func, "__name__", node_name)
    return wrapped


def run_graph_once(graph: Any, initial_state: BIAgentState) -> tuple[BIAgentState, list[dict]]:
    """Stream a graph exactly once while collecting its final state and trace."""
    started = perf_counter()
    final_state: BIAgentState = dict(initial_state)
    trace: list[dict] = []

    for step in graph.stream(initial_state):
        for node_name, node_output in step.items():
            if not isinstance(node_output, dict):
                continue
            final_state.update(node_output)
            trace_output = {
                key: value
                for key, value in node_output.items()
                if key != "node_timings"
            }
            trace.append({"node": node_name, **trace_output})

    final_state["total_duration_ms"] = round((perf_counter() - started) * 1000, 3)
    return final_state, trace
