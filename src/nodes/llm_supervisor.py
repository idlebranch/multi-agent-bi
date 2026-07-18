"""Experimental recovery supervisor.

Normal workflow transitions remain deterministic. The LLM is consulted only
after a recoverable error to choose between the bounded retry and a graceful
stop. This keeps the experiment while avoiding an LLM call for every hop.
"""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.config import get_llm
from src.guardrails import untrusted_text_block
from src.policy import build_routing_update
from src.routing import decide_next_node
from src.state import BIAgentState


ValidNode = Literal[
    "schema_linking",
    "sql_generation",
    "sql_review",
    "sql_validation",
    "sql_execution",
    "format_answer",
]


@tool
def route_to(next_node: ValidNode, reason: str) -> str:
    """Choose the next workflow node and briefly explain the decision."""
    return f"route to {next_node}: {reason}"


SYSTEM_PROMPT = """You are a recovery supervisor for a BI workflow.
The normal sequence is controlled by deterministic code. You are called only
after a failure. Choose either the suggested bounded retry or format_answer.
Always call route_to. Never select a node outside the allowed choices.
"""


def llm_supervisor_node(state: BIAgentState) -> dict:
    current_iteration = state.get("iteration", 0) + 1
    current_state = {**state, "iteration": current_iteration}
    fallback = decide_next_node(current_state)

    decided_node = fallback.next_node
    reason = fallback.reason
    policy = "deterministic"

    recoverable_error = bool(state.get("error")) and fallback.next_node in {
        "schema_linking",
        "sql_generation",
    }
    if recoverable_error:
        allowed = {fallback.next_node, "format_answer"}
        summary = {
            "question_data": untrusted_text_block(
                "user_question", str(state.get("question", "")), max_chars=2000
            ),
            "error_data": untrusted_text_block(
                "workflow_error", str(state.get("error", "")), max_chars=2000
            ),
            "error_source": state.get("error_source"),
            "visit_count": state.get("visit_count", {}),
            "suggested_retry": fallback.next_node,
            "allowed_choices": sorted(allowed),
        }
        try:
            response = get_llm(0.0).bind_tools([route_to]).invoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=json.dumps(summary, ensure_ascii=False, indent=2)),
                ]
            )
            if response.tool_calls:
                args = response.tool_calls[0].get("args", {})
                candidate = str(args.get("next_node", "")).strip()
                if candidate in allowed:
                    decided_node = candidate
                    reason = str(args.get("reason", "")).strip() or fallback.reason
                    policy = "llm_recovery"
        except Exception as exc:
            reason = f"{fallback.reason}; recovery model unavailable: {exc}"

    return build_routing_update(
        state,
        iteration=current_iteration,
        candidate=decided_node,
        reason=reason,
        routing_policy=policy,
    )


def llm_supervisor_router(state: BIAgentState) -> str:
    return state.get("next_node", "format_answer")
