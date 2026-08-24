"""Case loading and validation for the BI benchmark."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_BUSINESS_FIELDS = {
    "case_id",
    "category",
    "difficulty",
    "question",
    "expected_behavior",
    "gold_sql",
    "expected_result",
    "answer_assertions",
    "notes",
}
REQUIRED_SAFETY_FIELDS = {
    "case_id",
    "attack_type",
    "prompt",
    "expected_action",
    "database_must_not_execute",
    "expected_status",
}
DIFFICULTIES = {"easy", "medium", "hard"}


def _load_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"case file must contain a JSON array: {path}")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"every case must be an object: {path}")
    return payload


def load_business_cases(path: Path) -> list[dict[str, Any]]:
    cases = _load_json(path)
    seen: set[str] = set()
    for index, case in enumerate(cases, start=1):
        missing = REQUIRED_BUSINESS_FIELDS - case.keys()
        if missing:
            raise ValueError(f"business case #{index} missing: {sorted(missing)}")
        case_id = str(case["case_id"])
        if case_id in seen:
            raise ValueError(f"duplicate business case_id: {case_id}")
        seen.add(case_id)
        if case["difficulty"] not in DIFFICULTIES:
            raise ValueError(f"invalid difficulty in {case_id}: {case['difficulty']}")
        if case["expected_behavior"] == "query" and not case["gold_sql"]:
            raise ValueError(f"query case requires gold_sql: {case_id}")
        if not isinstance(case["answer_assertions"], dict):
            raise ValueError(f"answer_assertions must be an object: {case_id}")
    return cases


def load_safety_cases(path: Path) -> list[dict[str, Any]]:
    cases = _load_json(path)
    seen: set[str] = set()
    for index, case in enumerate(cases, start=1):
        missing = REQUIRED_SAFETY_FIELDS - case.keys()
        if missing:
            raise ValueError(f"safety case #{index} missing: {sorted(missing)}")
        case_id = str(case["case_id"])
        if case_id in seen:
            raise ValueError(f"duplicate safety case_id: {case_id}")
        seen.add(case_id)
        if case["database_must_not_execute"] is not True:
            raise ValueError(f"safety case must forbid database execution: {case_id}")
    return cases


def apply_evaluation_overrides(
    cases: list[dict[str, Any]], path: Path
) -> list[dict[str, Any]]:
    """Apply explicit evaluator policy corrections without changing gold values."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"evaluation overrides must be an object: {path}")
    by_id = {str(case["case_id"]): dict(case) for case in cases}
    unknown = set(payload) - set(by_id)
    if unknown:
        raise ValueError(f"evaluation overrides reference unknown cases: {sorted(unknown)}")
    for case_id, override in payload.items():
        if not isinstance(override, dict):
            raise ValueError(f"evaluation override must be an object: {case_id}")
        by_id[case_id].update(override)
        by_id[case_id]["evaluation_override_applied"] = True
    return [by_id[str(case["case_id"])] for case in cases]
