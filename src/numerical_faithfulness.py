"""Deterministic protection against percentage scale hallucinations."""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from src.guardrails import sanitize_result_rows


_PERCENT_COLUMN = re.compile(r"(?:pct|percent|percentage|rate|百分比|率)", re.IGNORECASE)
_PERCENT_CLAIM = re.compile(
    r"(?<![\d.])([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*%"
)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _percentage_values(rows: Sequence[dict[str, Any]]) -> list[Decimal]:
    return [
        number
        for row in rows
        for key, value in row.items()
        if _PERCENT_COLUMN.search(str(key))
        for number in [_decimal(value)]
        if number is not None
    ]


def _percentage_claims(answer: str) -> list[Decimal]:
    return [
        number
        for match in _PERCENT_CLAIM.finditer(answer)
        for number in [_decimal(match.group(1))]
        if number is not None
    ]


def _matches(value: Decimal, expected: Decimal) -> bool:
    return math.isclose(float(value), float(expected), rel_tol=1e-4, abs_tol=0.02)


def enforce_numerical_faithfulness(
    answer: str,
    rows: Sequence[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Replace prose only when a percent claim contradicts raw percentage fields."""
    expected = _percentage_values(rows)
    claims = _percentage_claims(answer)
    if not expected or not claims:
        return answer, {
            "status": "not_applicable",
            "percentage_claim_count": len(claims),
            "mismatch_count": 0,
        }

    mismatches = [
        claim for claim in claims if not any(_matches(claim, value) for value in expected)
    ]
    if not mismatches:
        return answer, {
            "status": "passed",
            "percentage_claim_count": len(claims),
            "mismatch_count": 0,
        }

    safe_rows = sanitize_result_rows(rows, for_llm=False, max_rows=10)
    corrected = (
        "检测到回答中的百分比与查询返回值存在缩放冲突。"
        "为避免数值失真，以下直接返回数据库原始结果；名称含 pct、percent 或 rate "
        "的字段按查询返回值作为百分比，不再乘以 100："
        + json.dumps(safe_rows, ensure_ascii=False)
    )
    return corrected, {
        "status": "corrected",
        "percentage_claim_count": len(claims),
        "mismatch_count": len(mismatches),
    }
