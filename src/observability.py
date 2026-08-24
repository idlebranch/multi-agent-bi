"""Safe, dependency-free request observability for the BI workflow."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from time import perf_counter
from typing import Any


REQUEST_LOGGER = logging.getLogger("bi_agent.request")


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_token_usage(response: Any) -> dict[str, int] | None:
    """Return provider-reported token counts, never locally estimated counts."""
    candidates: list[Mapping[str, Any]] = []
    usage_metadata = getattr(response, "usage_metadata", None)
    if isinstance(usage_metadata, Mapping):
        candidates.append(usage_metadata)
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, Mapping):
        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, Mapping):
            candidates.append(token_usage)

    aliases = {
        "prompt_tokens": ("prompt_tokens", "input_tokens"),
        "completion_tokens": ("completion_tokens", "output_tokens"),
        "total_tokens": ("total_tokens",),
    }
    for candidate in candidates:
        normalized: dict[str, int] = {}
        for canonical, names in aliases.items():
            for name in names:
                value = _integer(candidate.get(name))
                if value is not None and value >= 0:
                    normalized[canonical] = value
                    break
        if normalized:
            if "total_tokens" not in normalized and {
                "prompt_tokens",
                "completion_tokens",
            } <= normalized.keys():
                normalized["total_tokens"] = (
                    normalized["prompt_tokens"] + normalized["completion_tokens"]
                )
            return normalized
    return None


def invoke_llm_observed(
    observations: list[dict[str, Any]],
    stage: str,
    invocation: Callable[[], Any],
) -> Any:
    """Invoke one LLM stage and append safe timing/provider usage metadata."""
    started = perf_counter()
    try:
        response = invocation()
    except Exception:
        observations.append(
            {
                "stage": stage,
                "status": "failed",
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "token_usage": None,
            }
        )
        raise
    observations.append(
        {
            "stage": stage,
            "status": "succeeded",
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "token_usage": extract_token_usage(response),
        }
    )
    return response


def summarize_llm_observations(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize actual workflow invoke calls without claiming HTTP request counts."""
    breakdown = Counter(str(item.get("stage", "unknown")) for item in observations)
    available = [
        item["token_usage"]
        for item in observations
        if isinstance(item.get("token_usage"), Mapping)
    ]
    usage: dict[str, int] | None = None
    if available:
        usage = {
            key: sum(int(item.get(key, 0)) for item in available)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        usage.update(
            {
                "reported_calls": len(available),
                "unreported_calls": len(observations) - len(available),
            }
        )
    availability = (
        "available"
        if observations and len(available) == len(observations)
        else "partial"
        if available
        else "unavailable"
    )
    sql_generation_calls = breakdown.get("sql_generation", 0)
    return {
        "llm_stage_calls": len(observations),
        "llm_stage_breakdown": dict(sorted(breakdown.items())),
        "sql_repair_llm_calls": max(0, sql_generation_calls - 1),
        "provider_request_count": None,
        "token_usage_availability": availability,
        "token_usage": usage,
    }


def _sha256_text(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def build_safe_run_log(state: Mapping[str, Any]) -> dict[str, Any]:
    """Build a correlation record that excludes prompts, SQL, results, and errors."""
    question = str(state.get("question", ""))
    sql = str(state.get("sql", ""))
    llm = summarize_llm_observations(state.get("llm_stage_calls", []))
    routing = [
        {
            "decided": str(item.get("decided", "")),
            "policy": str(item.get("policy", "")),
        }
        for item in state.get("routing_history", [])
        if isinstance(item, Mapping)
    ]
    review = [
        {
            "code": str(item.get("code", "")),
            "severity": str(item.get("severity", "")),
        }
        for item in state.get("review_issues", [])
        if isinstance(item, Mapping)
    ]
    node_timings = [
        {
            "node": str(item.get("node", "")),
            "attempt": int(item.get("attempt", 0)),
            "duration_ms": float(item.get("duration_ms", 0.0)),
            "status": str(item.get("status", "")),
        }
        for item in state.get("node_timings", [])
        if isinstance(item, Mapping)
    ]
    error_sources = sorted(
        {
            str(item.get("source", ""))
            for item in state.get("error_history", [])
            if isinstance(item, Mapping) and item.get("source")
        }
    )
    return {
        "event": "bi_request_completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": str(state.get("request_id", "")),
        "run_id": str(state.get("run_id", "")),
        "response_status": str(state.get("response_status", "pending")),
        "request_status": str(state.get("request_status", "ready")),
        "question_sha256": _sha256_text(question),
        "question_length": len(question),
        "sql_sha256": _sha256_text(sql) if sql else None,
        "sql_length": len(sql),
        "nodes": node_timings,
        "routing_decisions": routing,
        "review_status": str(state.get("review_status", "not_started")),
        "review_decisions": review,
        "repair_count": max(0, len(state.get("sql_attempt_history", [])) - 1),
        "validation_status": str(state.get("validation_status", "not_started")),
        "execution_status": str(state.get("execution_status", "not_started")),
        "execution_error_code": str(state.get("execution_error_code", "")),
        "db_capacity_wait_ms": float(state.get("db_capacity_wait_ms", 0.0)),
        "result_row_count": int(state.get("result_row_count", 0)),
        "result_truncated": bool(state.get("result_truncated", False)),
        "error": bool(state.get("error_history")),
        "error_sources": error_sources,
        "total_latency_ms": float(state.get("total_duration_ms", 0.0)),
        "schema_context_metrics": dict(state.get("schema_context_metrics", {})),
        "numerical_faithfulness": dict(state.get("numerical_faithfulness", {})),
        **llm,
    }


def serialize_safe_run_log(state: Mapping[str, Any]) -> str:
    return json.dumps(
        build_safe_run_log(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def log_run_summary(state: Mapping[str, Any]) -> None:
    REQUEST_LOGGER.info("%s", serialize_safe_run_log(state))
