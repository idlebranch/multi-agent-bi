"""Deterministic SQL safety and database validation stage."""

from __future__ import annotations

from src.policy import require_tool
from src.state import BIAgentState, record_error
from src.tools.db_tools import validate_sql


def sql_validation_node(state: BIAgentState) -> dict:
    sql = state.get("sql", "")
    if not sql:
        message = "SQL is empty"
        return {
            "sql_validated": False,
            "validation_status": "failed",
            **record_error(state, "sql_validation", message),
        }

    require_tool("sql_validation", "validate_sql")
    result = validate_sql(sql)
    if result["valid"]:
        return {
            "sql_validated": True,
            "validation_status": "succeeded",
            "error": "",
            "error_source": "",
        }

    message = f"SQL validation failed: {result['error']}"
    return {
        "sql_validated": False,
        "validation_status": "failed",
        **record_error(state, "sql_validation", message),
    }
