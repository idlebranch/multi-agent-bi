"""Deterministic checks for the analyst's natural-language answer."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


_NUMERIC_TOKEN = re.compile(
    r"(?<![\d.])(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)(?P<unit>[万亿]?)%?"
)
_ENTITY_ALIASES = {
    "sao paulo": ("圣保罗",),
    "curitiba": ("库里蒂巴",),
    "rio de janeiro": ("里约热内卢",),
    "belo horizonte": ("贝洛奥里藏特",),
    "ribeirao preto": ("里贝朗普雷图",),
    "late": ("超时", "延迟"),
    "on_time": ("按时", "准时"),
    "credit_card": ("信用卡",),
    "debit_card": ("借记卡",),
}


def _normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"(\d{4})\s*年\s*(\d{1,2})\s*月", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}", value)
    value = re.sub(r"(\d{4})\s*年?\s*q\s*([1-4])", r"\1-q\2", value)
    value = re.sub(r"(\d{4})\s*年?\s*第?\s*([1-4])\s*季度", r"\1-q\2", value)
    return value


def _entity_candidates(value: str) -> tuple[str, ...]:
    normalized = _normalize_text(value)
    aliases = _ENTITY_ALIASES.get(normalized, ())
    return (normalized, *(_normalize_text(alias) for alias in aliases))


def _numbers(text: str) -> list[float]:
    values: list[float] = []
    for match in _NUMERIC_TOKEN.finditer(text):
        token = match.group("number").replace(",", "")
        try:
            value = float(token)
        except ValueError:
            continue
        if match.group("unit") == "万":
            value *= 10_000
        elif match.group("unit") == "亿":
            value *= 100_000_000
        values.append(value)
    return values


def _contains_number(text: str, expected: Any, tolerance: float) -> bool:
    try:
        target = float(expected)
    except (TypeError, ValueError):
        return False
    return any(
        math.isclose(value, target, rel_tol=1e-4, abs_tol=tolerance)
        for value in _numbers(text)
    )


def evaluate_answer(
    answer: str,
    assertions: dict[str, Any],
    *,
    gold_rows: list[dict[str, Any]] | None = None,
    response_status: str = "",
) -> dict[str, Any]:
    gold_rows = gold_rows or []
    folded = _normalize_text(answer)
    failures: list[str] = []

    expected_status = assertions.get("expected_status")
    if expected_status and response_status != expected_status:
        failures.append(f"status:{response_status}!={expected_status}")

    for term in assertions.get("required_terms", []):
        if _normalize_text(str(term)) not in folded:
            failures.append(f"missing_term:{term}")
    for alternatives in assertions.get("required_any_terms", []):
        if not any(_normalize_text(str(term)) in folded for term in alternatives):
            failures.append(f"missing_any_term:{alternatives}")
    for term in assertions.get("forbidden_terms", []):
        if _normalize_text(str(term)) in folded:
            failures.append(f"forbidden_term:{term}")

    for spec in assertions.get("required_gold_values", []):
        row_index = int(spec.get("row", 0))
        column = str(spec["column"])
        tolerance = float(spec.get("tolerance", assertions.get("numeric_tolerance", 0.02)))
        try:
            value = gold_rows[row_index][column]
        except (IndexError, KeyError):
            failures.append(f"invalid_gold_value_reference:{row_index}:{column}")
            continue
        if not _contains_number(answer, value, tolerance):
            failures.append(f"missing_gold_value:{column}={value}")

    for spec in assertions.get("required_gold_entities", []):
        row_index = int(spec.get("row", 0))
        column = str(spec["column"])
        try:
            value = str(gold_rows[row_index][column])
        except (IndexError, KeyError):
            failures.append(f"invalid_gold_entity_reference:{row_index}:{column}")
            continue
        if not any(candidate in folded for candidate in _entity_candidates(value)):
            failures.append(f"missing_gold_entity:{column}={value}")

    ordered = assertions.get("ordered_gold_entities")
    if ordered:
        column = str(ordered["column"])
        limit = min(int(ordered.get("limit", 3)), len(gold_rows))
        values = [str(row[column]) for row in gold_rows[:limit]]
        positions = [
            max((folded.find(candidate) for candidate in _entity_candidates(value)), default=-1)
            for value in values
        ]
        if any(position < 0 for position in positions):
            failures.append(f"missing_ordered_entities:{values}")
        elif positions != sorted(positions):
            failures.append(f"wrong_entity_order:{values}")

    return {"passed": not failures, "failures": failures}
