"""Bounded, read-only SQL execution stage."""

from __future__ import annotations

from src.config import MAX_RESULT_ROWS, SQL_TIMEOUT_SECONDS
from src.policy import policy_limit, require_tool
from src.state import BIAgentState, record_error
from src.tools.db_tools import execute_sql


def sql_execution_node(state: BIAgentState) -> dict:
    sql = state.get("sql", "")
    if not sql:
        message = "SQL is empty"
        return {
            "sql_result": [],
            "result_row_count": 0,
            "result_truncated": False,
            "execution_status": "failed",
            "execution_error_code": "invalid_sql",
            **record_error(state, "sql_execution", message),
        }

    require_tool("sql_execution", "execute_sql_read_only")
    result = execute_sql(
        sql,
        max_rows=min(MAX_RESULT_ROWS, int(policy_limit("result_rows", 200))),
        timeout_seconds=min(
            SQL_TIMEOUT_SECONDS,
            float(policy_limit("query_timeout_seconds", 5)),
        ),
    )
    if result["success"]:
        return {
            "sql_result": result["data"],
            "result_row_count": result["row_count"],
            "result_truncated": result["truncated"],
            "execution_status": "succeeded",
            "execution_error_code": "",
            "error": "",
            "error_source": "",
        }

    message = f"SQL execution failed: {result['error']}"
    return {
        "sql_result": [],
        "result_row_count": 0,
        "result_truncated": False,
        "execution_status": "failed",
        "execution_error_code": str(result.get("error_code") or "database_error"),
        **record_error(state, "sql_execution", message),
    }
