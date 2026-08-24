"""Deterministic first-pass failure taxonomy for benchmark results."""

from __future__ import annotations

import re
from typing import Any


def _tables_in_sql(sql: str) -> set[str]:
    return {
        match.casefold()
        for match in re.findall(r"\b(?:from|join)\s+[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)", sql, re.I)
    }


def classify_failure(
    case: dict[str, Any],
    state: dict[str, Any],
    *,
    execution_correct: bool | None,
    answer_correct: bool,
    exception: str = "",
) -> tuple[str, str]:
    if exception:
        return "execution_error", exception
    if case.get("expected_behavior") != "query":
        return "ambiguity_handling", f"expected {case.get('expected_behavior')}, got {state.get('response_status')}"
    if state.get("schema_status") in {"failed", "no_match"}:
        return "schema_linking", str(state.get("schema_reasoning") or state.get("error") or "schema stage failed")
    sql = str(state.get("sql") or "")
    if not sql:
        return "schema_linking", "no SQL generated"
    if state.get("review_status") == "failed":
        issues = state.get("review_issues", [])
        codes = {str(issue.get("code", "")) for issue in issues}
        if "join_fanout" in codes:
            return "wrong_join", str(state.get("review_feedback") or "join fan-out")
        if "wrong_metric" in codes or "missing_status_filter" in codes:
            return "metric_definition", str(state.get("review_feedback") or "reviewer rejected metric")
        return "reviewer_false_positive", str(state.get("review_feedback") or "reviewer rejected SQL")
    if state.get("validation_status") == "failed":
        error = str(state.get("error") or "")
        if "no such column" in error.casefold():
            return "hallucinated_column", error
        return "execution_error", error
    if state.get("execution_status") == "failed":
        repairs = max(0, len(state.get("sql_attempt_history", [])) - 1)
        category = "repair_failed" if repairs else "execution_error"
        return category, str(state.get("error") or state.get("execution_error_code") or "execution failed")
    if execution_correct is False:
        expected_tables = {str(table).casefold() for table in case.get("expected_tables", [])}
        actual_tables = _tables_in_sql(sql)
        if expected_tables and not expected_tables.intersection(actual_tables):
            return "wrong_table", f"expected one of {sorted(expected_tables)}, found {sorted(actual_tables)}"
        category = case.get("category")
        if category == "multi_table_join":
            return ("missing_join" if " join " not in f" {sql.casefold()} " else "wrong_join"), sql
        if category in {"time_series", "time_window"}:
            return "time_logic", sql
        if category == "governed_metric":
            return "metric_definition", sql
        if category == "ratio_metric":
            return "aggregation", sql
        if category in {"filtering_sorting", "complex_filter", "empty_result"}:
            return "filter", sql
        if case.get("ordering_required"):
            return "ordering", sql
        return "aggregation", sql
    if not answer_correct:
        return "answer_synthesis", "execution result matched but deterministic answer assertions failed"
    return "other", "unclassified failure"
