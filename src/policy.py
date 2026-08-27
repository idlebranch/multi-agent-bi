"""Fail-closed policy-as-code for agent context, tools, outputs, and handoffs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from src.contracts import HandoffEvent, PolicyDecision, ReviewIssue


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = PROJECT_ROOT / "policies" / "agent_policy.json"


class PolicyViolation(RuntimeError):
    """Raised when an agent attempts an action outside its policy contract."""


def _load_policy() -> dict[str, Any]:
    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load agent policy: {POLICY_PATH}") from exc
    if payload.get("default_action") != "deny":
        raise RuntimeError("agent policy must use default_action=deny")
    if not isinstance(payload.get("agents"), dict) or not isinstance(
        payload.get("transitions"), dict
    ):
        raise RuntimeError("agent policy is missing agents or transitions")
    return payload


POLICY = _load_policy()
POLICY_VERSION = str(POLICY["version"])


def policy_limit(name: str, default: int | float) -> int | float:
    value = POLICY.get("limits", {}).get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise RuntimeError(f"agent policy limit {name!r} must be a positive number")
    return value


def project_state_for_agent(agent: str, state: Mapping[str, Any]) -> dict[str, Any]:
    specification = POLICY["agents"].get(agent)
    if not specification:
        raise PolicyViolation(f"unknown agent: {agent}")
    allowed = set(specification.get("inputs", []))
    return {key: value for key, value in state.items() if key in allowed}


def require_tool(agent: str, tool_name: str) -> None:
    specification = POLICY["agents"].get(agent)
    allowed = set(specification.get("tools", [])) if specification else set()
    if tool_name not in allowed:
        raise PolicyViolation(f"{agent} is not allowed to call tool {tool_name}")


def require_action(action: str, *, approved: bool = False) -> None:
    approval_required = set(POLICY.get("human_approval_required_for", []))
    if action in approval_required and not approved:
        raise PolicyViolation(f"action {action} requires explicit human approval")


def validate_agent_update(agent: str, update: Mapping[str, Any]) -> None:
    specification = POLICY["agents"].get(agent)
    if not specification:
        raise PolicyViolation(f"unknown agent: {agent}")
    allowed = set(specification.get("outputs", []))
    unexpected = sorted(set(update) - allowed)
    if unexpected:
        raise PolicyViolation(
            f"{agent} attempted unauthorized state fields: {', '.join(unexpected)}"
        )

    valid_statuses = {"not_started", "succeeded", "failed", "no_match"}
    for key in (
        "schema_status",
        "sql_status",
        "review_status",
        "validation_status",
        "execution_status",
    ):
        if key in update and update[key] not in valid_statuses:
            raise PolicyViolation(f"{agent} returned invalid {key}: {update[key]!r}")

    if "sql" in update:
        sql = update["sql"]
        max_chars = int(policy_limit("agent_output_chars", 100_000))
        if not isinstance(sql, str) or len(sql) > max_chars:
            raise PolicyViolation(f"{agent} returned an invalid SQL value")
    if "sql_result" in update and not isinstance(update["sql_result"], list):
        raise PolicyViolation(f"{agent} returned a non-list sql_result")
    if "result_row_count" in update:
        count = update["result_row_count"]
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise PolicyViolation(f"{agent} returned an invalid result_row_count")
    if "review_issues" in update:
        issues = update["review_issues"]
        if not isinstance(issues, list):
            raise PolicyViolation(f"{agent} returned non-list review_issues")
        for issue in issues:
            ReviewIssue.model_validate(issue)
    if "final_answer" in update:
        answer = update["final_answer"]
        if not isinstance(answer, str) or len(answer) > 20_000:
            raise PolicyViolation(f"{agent} returned an invalid final_answer")
    for key in ("structured_intent", "query_plan", "analysis_result"):
        if key in update and not isinstance(update[key], dict):
            raise PolicyViolation(f"{agent} returned a non-dict {key}")


def transition_decision(from_agent: str, to_agent: str) -> PolicyDecision:
    allowed_destinations = set(POLICY["transitions"].get(from_agent, []))
    allowed = to_agent in allowed_destinations
    audit_agent = from_agent if from_agent in POLICY["transitions"] else "start"
    return PolicyDecision(
        policy_version=POLICY_VERSION,
        action=f"transition:{from_agent}->{to_agent}",
        allowed=allowed,
        reason=(
            "transition is allow-listed"
            if allowed
            else "transition is not present in the policy allow-list"
        ),
        agent=audit_agent,
    )


def node_output_decision(agent: str, *, allowed: bool, reason: str) -> PolicyDecision:
    return PolicyDecision(
        policy_version=POLICY_VERSION,
        action=f"state_update:{agent}",
        allowed=allowed,
        reason=reason,
        agent=agent,
    )


def build_routing_update(
    state: Mapping[str, Any],
    *,
    iteration: int,
    candidate: str,
    reason: str,
    routing_policy: str,
) -> dict[str, Any]:
    """Authorize and audit one logical handoff; deny invalid routes by terminating."""
    from_agent = str(state.get("current_agent", "start"))
    decision = transition_decision(from_agent, candidate)
    target = candidate
    route_reason = reason
    if not decision.allowed:
        target = "format_answer"
        route_reason = f"policy denied {from_agent}->{candidate}: {decision.reason}"

    policy_decisions = list(state.get("policy_decisions", []))
    policy_decisions.append(decision.model_dump(mode="json"))

    handoffs = list(state.get("handoff_history", []))
    handoff_from = from_agent if from_agent in POLICY["transitions"] else "start"
    handoffs.append(
        HandoffEvent(
            run_id=str(state.get("run_id", "unknown-run")),
            from_agent=handoff_from,
            to_agent=target,
            reason_code=routing_policy,
            reason=route_reason,
            attempt=iteration,
            policy_version=POLICY_VERSION,
        ).model_dump(mode="json")
    )

    history = list(state.get("routing_history", []))
    history.append(
        {
            "iteration": iteration,
            "decided": target,
            "reason": route_reason,
            "policy": routing_policy if decision.allowed else "policy_denied",
        }
    )
    update: dict[str, Any] = {
        "iteration": iteration,
        "next_node": target,
        "routing_history": history,
        "handoff_history": handoffs,
        "policy_decisions": policy_decisions,
    }
    if target == "format_answer" and state.get("execution_status") != "succeeded":
        update["terminal_reason"] = route_reason
    return update
