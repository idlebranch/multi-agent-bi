"""Deterministic post-query result analysis (no LLM, no Pandas).

Computes analysis facts (trend, ranking, composition, comparison, change,
basic anomaly) directly from the SQL result rows so that the answer formatter
explains measured numbers instead of re-deriving them from prose.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from src.contracts import AnalysisResult


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("%", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _is_numeric_column(rows: Sequence[dict[str, Any]], column: str) -> bool:
    values = [_decimal(row.get(column)) for row in rows]
    return any(v is not None for v in values) and all(v is not None for v in values)


def _columns(rows: Sequence[dict[str, Any]]) -> list[str]:
    return list(rows[0].keys()) if rows else []


def _metric_column(rows: Sequence[dict[str, Any]]) -> str | None:
    numeric = [c for c in _columns(rows) if _is_numeric_column(rows, c)]
    if not numeric:
        return None
    # Prefer a metric-looking name; otherwise the last numeric column.
    for c in numeric:
        if re.search(r"count|gmv|value|price|pct|rate|sum|total|avg|qty|金额|数量|销售额", c, re.I):
            return c
    return numeric[-1]


def _label_column(rows: Sequence[dict[str, Any]], metric_col: str | None) -> str | None:
    for c in _columns(rows):
        if c != metric_col:
            return c
    return None


def _month_key(value: Any) -> str:
    text = str(value)
    match = re.search(r"(20\d{2})[-/]?(\d{1,2})?", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}" if match.group(2) else match.group(1)
    return text


def _fmt(value: Decimal, places: int = 2) -> str:
    return f"{value:,.{places}f}".rstrip("0").rstrip(".")


def _pct(value: Decimal, places: int = 2) -> str:
    return f"{value:.{places}f}%"


def analyze_result(
    rows: Sequence[dict[str, Any]],
    analysis_type: str,
    question: str = "",
) -> AnalysisResult:
    rows = list(rows)
    if not rows:
        return AnalysisResult(
            analysis_type=analysis_type,
            summary="",
            facts=[],
            warnings=["no rows to analyze"],
        )

    metric_col = _metric_column(rows)
    label_col = _label_column(rows, metric_col)
    facts: list[str] = []
    warnings: list[str] = []

    if metric_col is None:
        return AnalysisResult(
            analysis_type=analysis_type,
            summary="",
            facts=[],
            warnings=["no numeric column found for analysis"],
        )

    pairs = sorted(
        [(str(row.get(label_col, "")), _decimal(row.get(metric_col))) for row in rows],
        key=lambda item: _month_key(item[0]),
    )
    pairs = [(label, value) for label, value in pairs if value is not None]
    if not pairs:
        return AnalysisResult(analysis_type=analysis_type, summary="", facts=[], warnings=["no numeric values"])

    values = [value for _, value in pairs]
    total = sum(values, Decimal(0))
    count = len(values)

    if analysis_type == "trend" or (analysis_type == "summary" and count > 1 and any(_month_key(label) for label, _ in pairs)):
        first_label, first = pairs[0]
        last_label, last = pairs[-1]
        peak_label, peak = max(pairs, key=lambda item: item[1])
        trough_label, trough = min(pairs, key=lambda item: item[1])
        facts.append(f"{last_label} {metric_col} = {_fmt(last)}")
        facts.append(f"峰值 {peak_label} = {_fmt(peak)}，谷值 {trough_label} = {_fmt(trough)}")
        if first != 0:
            change = (last - first) / first * 100
            facts.append(f"{first_label} → {last_label} 变化 {_fmt(change)}%")
        for index in range(1, len(pairs)):
            prev_label, prev = pairs[index - 1]
            label, curr = pairs[index]
            if prev != 0:
                mom = (curr - prev) / prev * 100
                facts.append(f"环比 {prev_label} → {label}: {_fmt(mom)}%")
        summary = f"{count} 个时间点的 {metric_col} 序列"

    elif analysis_type == "ranking" or (analysis_type == "summary" and count > 1):
        ranked = sorted(pairs, key=lambda item: item[1], reverse=True)
        top_label, top = ranked[0]
        bottom_label, bottom = ranked[-1]
        facts.append(f"最高：{top_label} = {_fmt(top)}")
        facts.append(f"最低：{bottom_label} = {_fmt(bottom)}")
        if total > 0:
            top3 = sum(v for _, v in ranked[:3])
            facts.append(f"前 3 名合计占比 {_pct(top3 / total * 100)}")
            facts.append(f"最高与次高差距：{_fmt(top - (ranked[1][1] if len(ranked) > 1 else top))}")
        for label, value in ranked[:3]:
            facts.append(f"Top: {label} = {_fmt(value)}")
        summary = f"{count} 个分组的 {metric_col} 排名"

    elif analysis_type == "composition":
        for label, value in pairs:
            if total > 0:
                facts.append(f"{label} 占 {_pct(value / total * 100)}")
        if total > 0:
            largest_label, largest = max(pairs, key=lambda item: item[1])
            facts.append(f"最大贡献：{largest_label} ({_pct(largest / total * 100)})")
        facts.append(f"总计 {metric_col} = {_fmt(total)}")
        summary = f"{metric_col} 构成"

    elif analysis_type in {"comparison", "change"} and count == 2:
        (label_a, value_a), (label_b, value_b) = pairs
        diff = value_b - value_a
        facts.append(f"{label_a} = {_fmt(value_a)}，{label_b} = {_fmt(value_b)}")
        facts.append(f"差值 = {_fmt(diff)}")
        if value_a != 0:
            facts.append(f"变化率 = {_pct(diff / value_a * 100)}")
        summary = f"{label_a} vs {label_b}"

    else:
        if count == 1:
            facts.append(f"{metric_col} = {_fmt(values[0])}")
            summary = f"{metric_col} 汇总"
        else:
            facts.append(f"总计 {metric_col} = {_fmt(total)}")
            facts.append(f"平均 = {_fmt(total / count)}")
            summary = f"{metric_col} 汇总"

    return AnalysisResult(analysis_type=analysis_type, summary=summary, facts=facts, warnings=warnings)
