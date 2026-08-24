"""Load the canonical PostgreSQL gold SQL for business benchmark cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_POSTGRES_GOLD = Path(__file__).resolve().parent / "cases" / "postgres_gold.json"


def load_postgres_gold(
    cases: list[dict[str, Any]], path: Path = DEFAULT_POSTGRES_GOLD
) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    portable = set(map(str, payload.get("portable_case_ids", [])))
    translated = {
        str(case_id): str(sql) for case_id, sql in payload.get("queries", {}).items()
    }
    query_cases = {
        str(case["case_id"]): case
        for case in cases
        if case["expected_behavior"] == "query"
    }
    if portable & translated.keys():
        raise ValueError("PostgreSQL gold IDs cannot be both portable and translated")
    covered = portable | translated.keys()
    if covered != query_cases.keys():
        missing = sorted(query_cases.keys() - covered)
        unknown = sorted(covered - query_cases.keys())
        raise ValueError(f"PostgreSQL gold coverage mismatch: missing={missing} unknown={unknown}")
    return {
        case_id: translated.get(case_id, str(case["gold_sql"]))
        for case_id, case in query_cases.items()
    }
