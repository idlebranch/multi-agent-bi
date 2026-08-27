"""Shared, explicit state for the BI workflow."""

from __future__ import annotations

from time import perf_counter
from typing import Literal, TypedDict
from uuid import uuid4

from src.config import DEFAULT_MAX_ITERATIONS, get_data_as_of_date
from src.guardrails import normalize_untrusted_text, redact_secrets, screen_user_question
from src.intent import build_structured_intent, clarification_decision


StageStatus = Literal["not_started", "succeeded", "failed", "no_match"]
ExecutionStatus = Literal["not_started", "succeeded", "failed"]
RequestStatus = Literal[
    "ready",
    "clarification_required",
    "out_of_scope",
    "rejected",
]
ResponseStatus = Literal[
    "pending",
    "success",
    "clarification",
    "out_of_scope",
    "rejected",
    "no_data",
    "failed",
]


class BIAgentState(TypedDict, total=False):
    # Request context
    request_id: str
    run_id: str
    question: str
    as_of_date: str
    input_guard_status: Literal["passed", "rejected"]
    input_risk_flags: list[str]
    request_status: RequestStatus
    request_message: str
    clarification_options: list[dict]

    # Structured intent / governed query plan / result analysis
    structured_intent: dict
    query_plan: dict
    analysis_result: dict

    # Catalog / schema selection
    relevant_tables: list[str]
    relevant_columns: dict[str, list[str]]
    schema_context_metrics: dict[str, int]
    schema_status: StageStatus
    schema_reasoning: str
    schema_refresh_count: int

    # SQL lifecycle
    sql: str
    sql_status: StageStatus
    review_status: StageStatus
    review_feedback: str
    review_issues: list[dict]
    sql_attempt_history: list[dict]
    sql_validated: bool
    validation_status: StageStatus

    # Execution lifecycle. Empty results are represented by a succeeded status
    # plus result_row_count=0, never by an implicit falsey-state check.
    sql_result: list[dict]
    result_row_count: int
    result_truncated: bool
    execution_status: ExecutionStatus
    execution_error_code: str
    db_capacity_wait_ms: float

    # Provider-reported observability. Token counts remain absent when the
    # provider does not return usage metadata; they are never estimated.
    llm_stage_calls: list[dict]

    # Error and recovery audit trail
    error: str
    error_source: str
    error_history: list[dict]

    # Orchestration
    iteration: int
    max_iterations: int
    next_node: str
    current_agent: str
    terminal_reason: str
    routing_history: list[dict]
    handoff_history: list[dict]
    policy_decisions: list[dict]
    visit_count: dict[str, int]
    node_timings: list[dict]
    last_node_timing: dict
    total_duration_ms: float

    # Response
    final_answer: str
    response_status: ResponseStatus
    numerical_faithfulness: dict


def create_initial_state(
    question: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    *,
    as_of_date: str | None = None,
) -> BIAgentState:
    """Build one canonical initial state for API, CLI, and tests."""
    guard_started = perf_counter()
    screening = screen_user_question(question)
    guard_duration_ms = round((perf_counter() - guard_started) * 1000, 3)
    intent = build_structured_intent(screening["question"])
    intent_dict = intent.model_dump(mode="json")
    request_status = screening["request_status"]
    request_message = screening["request_message"]
    clarification_options = screening["clarification_options"]
    if screening["status"] == "passed" and request_status == "ready":
        clarification = clarification_decision(intent)
        if clarification["needs_clarification"]:
            request_status = "clarification_required"
            request_message = clarification["message"]
            clarification_options = clarification["options"]
    return {
        "request_id": uuid4().hex,
        "run_id": uuid4().hex,
        "question": screening["question"],
        "as_of_date": as_of_date or get_data_as_of_date(),
        "input_guard_status": screening["status"],
        "input_risk_flags": screening["risk_flags"],
        "request_status": request_status,
        "request_message": request_message,
        "clarification_options": clarification_options,
        "structured_intent": intent_dict,
        "query_plan": {},
        "analysis_result": {},
        "relevant_tables": [],
        "relevant_columns": {},
        "schema_context_metrics": {
            "available_table_count": 0,
            "available_column_count": 0,
            "selected_table_count": 0,
            "selected_column_count": 0,
            "catalog_context_chars": 0,
            "selected_schema_context_chars": 0,
        },
        "schema_status": "not_started",
        "schema_reasoning": "",
        "schema_refresh_count": 0,
        "sql": "",
        "sql_status": "not_started",
        "review_status": "not_started",
        "review_feedback": "",
        "review_issues": [],
        "sql_attempt_history": [],
        "sql_validated": False,
        "validation_status": "not_started",
        "sql_result": [],
        "result_row_count": 0,
        "result_truncated": False,
        "execution_status": "not_started",
        "execution_error_code": "",
        "db_capacity_wait_ms": 0.0,
        "llm_stage_calls": [],
        "error": "",
        "error_source": "",
        "error_history": [],
        "iteration": 0,
        "max_iterations": max_iterations,
        "next_node": "",
        "current_agent": "start",
        "terminal_reason": "",
        "routing_history": [],
        "handoff_history": [],
        "policy_decisions": [],
        "visit_count": {},
        "node_timings": [
            {
                "node": "input_guard",
                "attempt": 1,
                "duration_ms": guard_duration_ms,
                "status": screening["status"],
            }
        ],
        "last_node_timing": {},
        "total_duration_ms": 0.0,
        "final_answer": "",
        "response_status": "pending",
        "numerical_faithfulness": {
            "status": "not_started",
            "percentage_claim_count": 0,
            "mismatch_count": 0,
        },
    }


def record_error(state: BIAgentState, source: str, message: str) -> dict:
    """Return a consistent error update without discarding earlier failures."""
    message = redact_secrets(normalize_untrusted_text(message, max_chars=2000))
    history = list(state.get("error_history", []))
    history.append(
        {
            "iteration": state.get("iteration", 0),
            "source": source,
            "message": message,
        }
    )
    return {
        "error": message,
        "error_source": source,
        "error_history": history,
    }
