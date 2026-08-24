"""FastAPI entry point for the production BI Agent workflow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from src.config import (
    DEEPSEEK_API_KEY,
    DEFAULT_MAX_ITERATIONS,
    MAX_ALLOWED_ITERATIONS,
    get_active_dataset_manifest,
    get_data_as_of_date,
)
from src.graph import app as production_agent
from src.guardrails import sanitize_public_value, sanitize_result_rows
from src.policy import POLICY_VERSION, policy_limit
from src.state import BIAgentState, create_initial_state
from src.tools.db_tools import get_database_health_summary
from src.workflow import run_graph_once


LOGGER = logging.getLogger("bi_agent.api")
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
APP_VERSION = "4.0.0"
EFFECTIVE_MAX_ALLOWED_ITERATIONS = min(
    MAX_ALLOWED_ITERATIONS,
    int(policy_limit("workflow_iterations", 12)),
)

api = FastAPI(
    title="BI Agent API",
    description="Production read-only, bounded natural-language BI workflow",
    version=APP_VERSION,
)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    max_iterations: int = Field(
        default=DEFAULT_MAX_ITERATIONS,
        ge=5,
        le=EFFECTIVE_MAX_ALLOWED_ITERATIONS,
    )


class AskResponse(BaseModel):
    run_id: str
    question: str
    version: str
    response_status: str
    request_status: str
    final_answer: str
    clarification_options: list[dict]
    sql: str
    sql_result: list
    iteration: int
    total_duration_ms: float
    repair_count: int
    auto_repaired: bool
    relevant_tables: list
    relevant_columns: dict
    timeline: list[dict]
    trace: list
    routing_history: list
    schema_status: str
    sql_status: str
    validation_status: str
    review_status: str
    review_feedback: str
    review_issues: list
    execution_status: str
    execution_error_code: str
    result_row_count: int
    result_truncated: bool
    error_history: list
    input_guard_status: str
    input_risk_flags: list
    handoff_history: list
    policy_decisions: list
    run_state: dict


_NODE_LABELS = {
    "input_guard": "输入防护",
    "schema_linking": "Schema Linking",
    "sql_generation": "SQL Writer",
    "sql_review": "SQL Reviewer",
    "sql_validation": "Safety Validator",
    "sql_execution": "Read-only Executor",
    "format_answer": "Analyst / Answer Formatter",
}

_NODE_STATUS_FIELDS = {
    "schema_linking": "schema_status",
    "sql_generation": "sql_status",
    "sql_review": "review_status",
    "sql_validation": "validation_status",
    "sql_execution": "execution_status",
    "format_answer": "response_status",
}


def _compact_trace(trace: list[dict], max_chars: int = 1200) -> list[dict]:
    compact: list[dict] = []
    for item in trace:
        cleaned: dict[str, Any] = {"node": item.get("node", "")}
        for key, value in item.items():
            if key in {"node", "node_timings"}:
                continue
            safe_value = sanitize_public_value(value, max_chars=max_chars)
            rendered = str(safe_value)
            cleaned[key] = (
                safe_value if len(rendered) <= max_chars else rendered[:max_chars] + "..."
            )
        compact.append(cleaned)
    return compact


def _timeline_summary(node: str, item: dict, final_state: BIAgentState) -> str:
    if node == "schema_linking":
        tables = item.get("relevant_tables", [])
        if item.get("schema_status") == "succeeded":
            return "已选择：" + "、".join(map(str, tables))
        if item.get("schema_status") == "no_match":
            return "当前数据目录不包含所需业务领域"
        return "数据目录匹配未完成"
    if node == "sql_generation":
        attempt = len(item.get("sql_attempt_history", []))
        return f"已生成第 {attempt} 个 SQL 候选" if item.get("sql") else "SQL 生成失败"
    if node == "sql_review":
        issues = item.get("review_issues", [])
        if item.get("review_status") == "succeeded":
            return "业务口径、JOIN、聚合与日期范围检查通过"
        return f"发现 {len(issues)} 个问题，将按有限预算自动修复"
    if node == "sql_validation":
        return (
            "单语句只读检查与 SQLite EXPLAIN 通过"
            if item.get("validation_status") == "succeeded"
            else "SQL 未通过只读安全校验"
        )
    if node == "sql_execution":
        if item.get("execution_status") == "succeeded":
            return f"只读查询完成，返回 {item.get('result_row_count', 0)} 行"
        return "数据库未执行成功"
    if node == "format_answer":
        status = item.get("response_status", final_state.get("response_status", "failed"))
        summaries = {
            "success": "业务回答已生成",
            "clarification": "已请求用户明确业务指标",
            "out_of_scope": "已说明当前数据库的数据范围",
            "rejected": "请求已被安全策略拒绝",
            "no_data": "查询成功，但没有匹配记录",
            "failed": "已生成安全的中文失败说明",
        }
        return summaries.get(str(status), "回答阶段已结束")
    return "节点已完成"


def _timeline_details(node: str, item: dict) -> dict[str, Any]:
    if node == "schema_linking":
        return {
            "tables": item.get("relevant_tables", []),
            "columns": item.get("relevant_columns", {}),
            "reasoning": item.get("schema_reasoning", ""),
        }
    if node == "sql_generation":
        return {"sql_generated": bool(item.get("sql"))}
    if node == "sql_review":
        return {
            "feedback": item.get("review_feedback", ""),
            "issues": item.get("review_issues", []),
        }
    if node == "sql_validation":
        return {"read_only_validated": bool(item.get("sql_validated", False))}
    if node == "sql_execution":
        return {
            "row_count": item.get("result_row_count", 0),
            "truncated": item.get("result_truncated", False),
            "error_code": item.get("execution_error_code", ""),
        }
    return {}


def _build_timeline(trace: list[dict], final_state: BIAgentState) -> list[dict]:
    guard_timing = next(
        (
            timing
            for timing in final_state.get("node_timings", [])
            if timing.get("node") == "input_guard"
        ),
        {},
    )
    guard_status = final_state.get("input_guard_status", "passed")
    timeline: list[dict] = [
        {
            "node": "input_guard",
            "label": _NODE_LABELS["input_guard"],
            "status": guard_status,
            "summary": (
                "请求类型：只读分析"
                if guard_status == "passed"
                else "检测到写入、注入或规则绕过请求"
            ),
            "duration_ms": guard_timing.get("duration_ms", 0.0),
            "attempt": 1,
            "details": {
                "risk_types": final_state.get("input_risk_flags", []),
            },
        }
    ]

    if guard_status == "rejected":
        timeline.extend(
            [
                {
                    "node": "sql_validation",
                    "label": _NODE_LABELS["sql_validation"],
                    "status": "rejected",
                    "summary": "输入防护已拒绝请求，未进入 SQL 安全校验",
                    "duration_ms": 0.0,
                    "attempt": 0,
                    "details": {"database_statement_executed": False},
                },
                {
                    "node": "sql_execution",
                    "label": _NODE_LABELS["sql_execution"],
                    "status": "not_started",
                    "summary": "数据库未执行任何语句",
                    "duration_ms": 0.0,
                    "attempt": 0,
                    "details": {"row_count": 0},
                },
            ]
        )

    for item in trace:
        node = str(item.get("node", ""))
        if node not in _NODE_LABELS or node == "input_guard":
            continue
        timing = item.get("last_node_timing", {})
        status_field = _NODE_STATUS_FIELDS.get(node, "")
        status = item.get(status_field, timing.get("status", "succeeded"))
        timeline.append(
            {
                "node": node,
                "label": _NODE_LABELS[node],
                "status": status,
                "summary": _timeline_summary(node, item, final_state),
                "duration_ms": timing.get("duration_ms", 0.0),
                "attempt": timing.get("attempt", 1),
                "details": sanitize_public_value(_timeline_details(node, item)),
            }
        )
    return timeline


def _public_run_state(final_state: BIAgentState) -> dict[str, Any]:
    keys = (
        "run_id",
        "question",
        "as_of_date",
        "request_status",
        "response_status",
        "input_guard_status",
        "input_risk_flags",
        "relevant_tables",
        "relevant_columns",
        "schema_status",
        "schema_reasoning",
        "sql_status",
        "review_status",
        "review_feedback",
        "review_issues",
        "sql_attempt_history",
        "validation_status",
        "execution_status",
        "execution_error_code",
        "result_row_count",
        "result_truncated",
        "iteration",
        "visit_count",
        "node_timings",
        "total_duration_ms",
        "terminal_reason",
    )
    return sanitize_public_value(
        {key: final_state.get(key) for key in keys},
        max_chars=4000,
    )


def _response_from_state(
    final_state: BIAgentState,
    trace: list[dict],
) -> AskResponse:
    attempts = final_state.get("sql_attempt_history", [])
    repair_count = max(0, len(attempts) - 1)
    compact_trace = _compact_trace(trace)
    validation_status = final_state.get("validation_status", "not_started")
    if final_state.get("input_guard_status") == "rejected":
        validation_status = "rejected"
    return AskResponse(
        run_id=final_state.get("run_id", ""),
        question=sanitize_public_value(final_state.get("question", "")),
        version=f"Production {APP_VERSION}",
        response_status=final_state.get("response_status", "failed"),
        request_status=final_state.get("request_status", "ready"),
        final_answer=sanitize_public_value(final_state.get("final_answer", "")),
        clarification_options=sanitize_public_value(
            final_state.get("clarification_options", [])
        ),
        sql=sanitize_public_value(final_state.get("sql", ""), max_chars=100_000),
        sql_result=sanitize_result_rows(
            final_state.get("sql_result", []), for_llm=False, max_rows=200
        ),
        iteration=final_state.get("iteration", 0),
        total_duration_ms=float(final_state.get("total_duration_ms", 0.0)),
        repair_count=repair_count,
        auto_repaired=repair_count > 0,
        relevant_tables=sanitize_public_value(final_state.get("relevant_tables", [])),
        relevant_columns=sanitize_public_value(final_state.get("relevant_columns", {})),
        timeline=_build_timeline(compact_trace, final_state),
        trace=compact_trace,
        routing_history=sanitize_public_value(
            final_state.get("routing_history", [])
        ),
        schema_status=final_state.get("schema_status", "not_started"),
        sql_status=final_state.get("sql_status", "not_started"),
        validation_status=validation_status,
        review_status=final_state.get("review_status", "not_started"),
        review_feedback=sanitize_public_value(
            final_state.get("review_feedback", "")
        ),
        review_issues=sanitize_public_value(final_state.get("review_issues", [])),
        execution_status=final_state.get("execution_status", "not_started"),
        execution_error_code=final_state.get("execution_error_code", ""),
        result_row_count=final_state.get("result_row_count", 0),
        result_truncated=final_state.get("result_truncated", False),
        error_history=sanitize_public_value(final_state.get("error_history", [])),
        input_guard_status=final_state.get("input_guard_status", "passed"),
        input_risk_flags=final_state.get("input_risk_flags", []),
        handoff_history=sanitize_public_value(final_state.get("handoff_history", [])),
        policy_decisions=sanitize_public_value(
            final_state.get("policy_decisions", [])
        ),
        run_state=_public_run_state(final_state),
    )


@api.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    initial_state = create_initial_state(req.question, req.max_iterations)
    try:
        final_state, trace = await run_in_threadpool(
            run_graph_once,
            production_agent,
            initial_state,
        )
    except Exception:
        LOGGER.exception("production workflow failed for run_id=%s", initial_state["run_id"])
        final_state = dict(initial_state)
        final_state.update(
            {
                "response_status": "failed",
                "final_answer": "系统暂时无法完成这次分析，请稍后重试。",
                "terminal_reason": "unexpected workflow failure",
            }
        )
        trace = []
    return _response_from_state(final_state, trace)


@api.get("/health")
def health(refresh: bool = Query(default=False)) -> dict[str, Any]:
    manifest, _ = get_active_dataset_manifest()
    try:
        diagnostics = get_database_health_summary(force_refresh=refresh)
        diagnostics.update(
            {
                "dataset": manifest.get("name", "custom_or_mock"),
                "as_of_date": get_data_as_of_date(),
            }
        )
        database_ready = diagnostics.get("status") == "ready"
        status = "ok" if database_ready else "degraded"
    except (FileNotFoundError, OSError, RuntimeError):
        LOGGER.exception("database health check failed")
        diagnostics = {
            "status": "unavailable",
            "message": "数据库暂时不可访问，请检查本地数据文件。",
            "read_only": True,
        }
        database_ready = False
        status = "degraded"

    llm_configured = bool(DEEPSEEK_API_KEY)
    return {
        "status": status,
        "service": "bi-agent-api",
        "version": APP_VERSION,
        "mode": "Production",
        "policy_version": POLICY_VERSION,
        "agent_ready": bool(database_ready and llm_configured),
        "llm": {
            "status": "not_checked" if llm_configured else "unavailable",
            "configured": llm_configured,
        },
        "database": diagnostics,
    }


@api.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    api.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(api, host="127.0.0.1", port=8000)
