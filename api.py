"""FastAPI entry point for the stable and experimental BI workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.config import (
    DEFAULT_MAX_ITERATIONS,
    MAX_ALLOWED_ITERATIONS,
    get_active_dataset_manifest,
    get_data_as_of_date,
)
from src.graph import app as stable_agent
from src.graph_v2 import app_v2 as experimental_agent
from src.guardrails import sanitize_public_value, sanitize_result_rows
from src.policy import policy_limit
from src.state import create_initial_state
from src.tools.db_tools import get_db_path
from src.workflow import run_graph_once


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
EFFECTIVE_MAX_ALLOWED_ITERATIONS = min(
    MAX_ALLOWED_ITERATIONS,
    int(policy_limit("workflow_iterations", 12)),
)

api = FastAPI(
    title="BI Agent API",
    description="Read-only, bounded natural-language BI workflow",
    version="3.1.0",
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    max_iterations: int = Field(
        default=DEFAULT_MAX_ITERATIONS,
        ge=5,
        le=EFFECTIVE_MAX_ALLOWED_ITERATIONS,
    )
    version: Literal["v1", "v2"] = "v1"


class AskResponse(BaseModel):
    run_id: str
    question: str
    version: str
    final_answer: str
    sql: str
    sql_result: list
    iteration: int
    relevant_tables: list
    trace: list
    routing_history: list
    schema_status: str
    validation_status: str
    review_status: str
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


def _compact_trace(trace: list[dict], max_chars: int = 1000) -> list[dict]:
    compact: list[dict] = []
    for item in trace:
        cleaned: dict = {"node": item.get("node", "")}
        for key, value in item.items():
            if key == "node":
                continue
            safe_value = sanitize_public_value(value, max_chars=max_chars)
            rendered = str(safe_value)
            cleaned[key] = (
                safe_value if len(rendered) <= max_chars else rendered[:max_chars] + "..."
            )
        compact.append(cleaned)
    return compact


@api.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    graph = experimental_agent if req.version == "v2" else stable_agent
    initial_state = create_initial_state(req.question, req.max_iterations)
    final_state, trace = await run_in_threadpool(run_graph_once, graph, initial_state)

    return AskResponse(
        run_id=final_state.get("run_id", ""),
        question=sanitize_public_value(final_state.get("question", req.question)),
        version=req.version,
        final_answer=sanitize_public_value(final_state.get("final_answer", "")),
        sql=sanitize_public_value(final_state.get("sql", ""), max_chars=100_000),
        sql_result=sanitize_result_rows(
            final_state.get("sql_result", []), for_llm=False, max_rows=200
        ),
        iteration=final_state.get("iteration", 0),
        relevant_tables=sanitize_public_value(final_state.get("relevant_tables", [])),
        trace=_compact_trace(trace),
        routing_history=final_state.get("routing_history", []),
        schema_status=final_state.get("schema_status", "not_started"),
        validation_status=final_state.get("validation_status", "not_started"),
        review_status=final_state.get("review_status", "not_started"),
        review_issues=sanitize_public_value(final_state.get("review_issues", [])),
        execution_status=final_state.get("execution_status", "not_started"),
        execution_error_code=final_state.get("execution_error_code", ""),
        result_row_count=final_state.get("result_row_count", 0),
        result_truncated=final_state.get("result_truncated", False),
        error_history=sanitize_public_value(final_state.get("error_history", [])),
        input_guard_status=final_state.get("input_guard_status", "passed"),
        input_risk_flags=final_state.get("input_risk_flags", []),
        handoff_history=sanitize_public_value(final_state.get("handoff_history", [])),
        policy_decisions=sanitize_public_value(final_state.get("policy_decisions", [])),
    )


@api.get("/health")
def health() -> dict:
    manifest, _ = get_active_dataset_manifest()
    try:
        database = get_db_path()
        database_status = {
            "status": "ready",
            "dataset": manifest.get("name", "custom_or_mock"),
            "file": database.name,
            "bytes": database.stat().st_size,
            "as_of_date": get_data_as_of_date(),
        }
        status = "ok"
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        database_status = {"status": "unavailable", "error": str(exc)}
        status = "degraded"
    return {
        "status": status,
        "service": "bi-agent-api",
        "version": "3.1.0",
        "database": database_status,
    }


@api.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    api.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(api, host="127.0.0.1", port=8000)
