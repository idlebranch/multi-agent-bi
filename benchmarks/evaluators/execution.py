"""Execution-result equivalence without SQL-string matching."""

from __future__ import annotations

import itertools
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def _normalize(value: Any) -> tuple[str, Any]:
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return ("float", value)
        return ("number", Decimal(str(value)))
    if isinstance(value, (date, datetime)):
        return ("datetime", value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat())
    text = str(value).strip()
    compact_number = text.replace(",", "")
    if _NUMBER.fullmatch(compact_number):
        try:
            return ("number", Decimal(compact_number))
        except InvalidOperation:
            pass
    iso_text = text.replace("T", " ").removesuffix("Z")
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        try:
            return ("date", date.fromisoformat(iso_text).isoformat())
        except ValueError:
            return ("text", text)
    return ("datetime", parsed.isoformat(sep=" "))


def _values_equal(left: Any, right: Any, *, abs_tol: float, rel_tol: float) -> bool:
    left_kind, left_value = _normalize(left)
    right_kind, right_value = _normalize(right)
    if left_kind == right_kind == "number":
        return math.isclose(
            float(left_value), float(right_value), rel_tol=rel_tol, abs_tol=abs_tol
        )
    if {left_kind, right_kind} <= {"date", "datetime"}:
        return str(left_value).replace(" 00:00:00", "") == str(right_value).replace(
            " 00:00:00", ""
        )
    return left_kind == right_kind and left_value == right_value


def _row_equal(
    gold: tuple[Any, ...],
    agent: tuple[Any, ...],
    *,
    abs_tol: float,
    rel_tol: float,
) -> bool:
    return len(gold) == len(agent) and all(
        _values_equal(left, right, abs_tol=abs_tol, rel_tol=rel_tol)
        for left, right in zip(gold, agent)
    )


def _rows_equal(
    gold: list[tuple[Any, ...]],
    agent: list[tuple[Any, ...]],
    *,
    ordered: bool,
    abs_tol: float,
    rel_tol: float,
) -> bool:
    if len(gold) != len(agent):
        return False
    if ordered:
        return all(
            _row_equal(left, right, abs_tol=abs_tol, rel_tol=rel_tol)
            for left, right in zip(gold, agent)
        )
    unmatched = list(agent)
    for gold_row in gold:
        for index, agent_row in enumerate(unmatched):
            if _row_equal(gold_row, agent_row, abs_tol=abs_tol, rel_tol=rel_tol):
                unmatched.pop(index)
                break
        else:
            return False
    return not unmatched


def compare_results(
    gold_rows: list[dict[str, Any]],
    agent_rows: list[dict[str, Any]],
    *,
    ordered: bool = False,
    abs_tol: float = 1e-6,
    rel_tol: float = 1e-7,
    allow_agent_extra_columns: bool = False,
) -> dict[str, Any]:
    """Compare two result sets while ignoring column order and preserving duplicates."""
    if not gold_rows or not agent_rows:
        passed = gold_rows == agent_rows
        return {
            "passed": passed,
            "reason": "both_empty" if passed else "empty_result_mismatch",
            "gold_row_count": len(gold_rows),
            "agent_row_count": len(agent_rows),
        }

    gold_columns = list(gold_rows[0])
    agent_columns = list(agent_rows[0])
    if len(gold_columns) != len(agent_columns) and not (
        allow_agent_extra_columns and len(agent_columns) > len(gold_columns)
    ):
        return {
            "passed": False,
            "reason": "column_count_mismatch",
            "gold_column_count": len(gold_columns),
            "agent_column_count": len(agent_columns),
            "gold_row_count": len(gold_rows),
            "agent_row_count": len(agent_rows),
        }

    if any(list(row) != gold_columns for row in gold_rows) or any(
        list(row) != agent_columns for row in agent_rows
    ):
        return {
            "passed": False,
            "reason": "inconsistent_result_columns",
            "gold_row_count": len(gold_rows),
            "agent_row_count": len(agent_rows),
        }

    gold_values = [tuple(row[column] for column in gold_columns) for row in gold_rows]
    agent_raw = [tuple(row[column] for column in agent_columns) for row in agent_rows]
    gold_column_count = len(gold_columns)
    agent_column_count = len(agent_columns)
    permutations = (
        itertools.permutations(range(agent_column_count), gold_column_count)
        if agent_column_count <= 8
        else [range(gold_column_count)]
    )
    for permutation in permutations:
        agent_values = [tuple(row[index] for index in permutation) for row in agent_raw]
        if _rows_equal(
            gold_values,
            agent_values,
            ordered=ordered,
            abs_tol=abs_tol,
            rel_tol=rel_tol,
        ):
            return {
                "passed": True,
                "reason": "equivalent",
                "gold_row_count": len(gold_rows),
                "agent_row_count": len(agent_rows),
                "column_mapping": [agent_columns[index] for index in permutation],
            }
    return {
        "passed": False,
        "reason": "value_or_row_mismatch",
        "gold_row_count": len(gold_rows),
        "agent_row_count": len(agent_rows),
    }


def compare_top_k_with_boundary_ties(
    gold_rows: list[dict[str, Any]],
    agent_rows: list[dict[str, Any]],
    *,
    metric_column: str,
    entity_columns: list[str],
    abs_tol: float = 0.02,
    rel_tol: float = 1e-7,
) -> dict[str, Any]:
    """Accept interchangeable entities tied at the top-k cutoff."""
    if not gold_rows or len(gold_rows) != len(agent_rows):
        return {
            "passed": False,
            "reason": "top_k_row_count_mismatch",
            "gold_row_count": len(gold_rows),
            "agent_row_count": len(agent_rows),
        }
    if metric_column not in gold_rows[0] or metric_column not in agent_rows[0]:
        return {"passed": False, "reason": "top_k_metric_column_missing"}
    cutoff = gold_rows[-1][metric_column]
    gold_mandatory = [
        row
        for row in gold_rows
        if not _values_equal(row[metric_column], cutoff, abs_tol=abs_tol, rel_tol=rel_tol)
    ]
    agent_mandatory = [
        row
        for row in agent_rows
        if not _values_equal(row[metric_column], cutoff, abs_tol=abs_tol, rel_tol=rel_tol)
    ]
    mandatory = compare_results(
        gold_mandatory,
        agent_mandatory,
        ordered=False,
        abs_tol=abs_tol,
        rel_tol=rel_tol,
    )
    if not mandatory["passed"]:
        return {"passed": False, "reason": "top_k_mandatory_rows_mismatch"}
    ignored = set(entity_columns)
    gold_boundary = [
        {key: value for key, value in row.items() if key not in ignored}
        for row in gold_rows
        if _values_equal(row[metric_column], cutoff, abs_tol=abs_tol, rel_tol=rel_tol)
    ]
    agent_boundary = [
        {key: value for key, value in row.items() if key not in ignored}
        for row in agent_rows
        if _values_equal(row[metric_column], cutoff, abs_tol=abs_tol, rel_tol=rel_tol)
    ]
    boundary = compare_results(
        gold_boundary,
        agent_boundary,
        ordered=False,
        abs_tol=abs_tol,
        rel_tol=rel_tol,
    )
    return {
        "passed": bool(boundary["passed"]),
        "reason": "equivalent_with_boundary_tie" if boundary["passed"] else "top_k_boundary_mismatch",
        "gold_row_count": len(gold_rows),
        "agent_row_count": len(agent_rows),
    }
