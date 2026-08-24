"""Compare frozen SQLite gold results with PostgreSQL gold results, without an LLM."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.run_benchmark import compare_case_results  # noqa: E402
from benchmarks.schema import apply_evaluation_overrides, load_business_cases  # noqa: E402
from benchmarks.sqlite_reference import (  # noqa: E402
    execute_sqlite,
    get_sqlite_reference_path,
    sqlite_database_fingerprint,
)
from src.tools.postgres_db_tools import execute_sql  # noqa: E402


CASES = PROJECT_ROOT / "benchmarks" / "cases" / "business_cases.json"
OVERRIDES = PROJECT_ROOT / "benchmarks" / "cases" / "evaluation_overrides.json"
POSTGRES_GOLD = PROJECT_ROOT / "benchmarks" / "cases" / "postgres_gold.json"
RESULTS = PROJECT_ROOT / "benchmarks" / "results"
KEY_TABLES = (
    "orders",
    "order_items",
    "payments",
    "reviews",
    "customers",
    "products",
    "sellers",
    "order_financials",
    "order_delivery_metrics",
    "product_sales",
    "category_sales_summary",
    "delivery_kpis",
    "payment_type_summary",
    "customer_order_summary",
)


def load_postgres_gold(
    cases: list[dict[str, Any]], path: Path = POSTGRES_GOLD
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


def _difference_type(case: dict[str, Any], comparison: dict[str, Any]) -> str:
    reason = str(comparison.get("reason", ""))
    sql = str(case.get("gold_sql", "")).casefold()
    if "column" in reason:
        return "schema mismatch"
    if any(token in sql for token in ("timestamp", "strftime", "substr(")):
        return "date semantics"
    if any(token in sql for token in ("round(", "avg(", "sum(", "100.0")):
        return "numeric semantics"
    return "other"


def run_parity(
    database_url: str,
    sqlite_path: Path,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    postgres_gold = load_postgres_gold(cases)
    records: list[dict[str, Any]] = []
    sqlite_executable = 0
    postgres_executable = 0
    parity_passed = 0
    comparable = 0

    for index, case in enumerate(cases, start=1):
        case_id = str(case["case_id"])
        print(f"[{index}/{len(cases)}] {case_id}", flush=True)
        if case["expected_behavior"] != "query":
            records.append(
                {
                    "case_id": case_id,
                    "sqlite_execution_ok": None,
                    "postgres_execution_ok": None,
                    "sqlite_result": None,
                    "postgres_result": None,
                    "parity_passed": None,
                    "difference_type": "not_applicable",
                    "notes": f"expected_behavior={case['expected_behavior']}",
                }
            )
            continue

        comparable += 1
        sqlite_result = execute_sqlite(
            str(case["gold_sql"]), sqlite_path, max_rows=10_000, timeout_seconds=30
        )
        postgres_result = execute_sql(
            postgres_gold[case_id],
            database_url,
            max_rows=10_000,
            timeout_seconds=30,
        )
        sqlite_ok = bool(sqlite_result["success"])
        postgres_ok = bool(postgres_result["success"])
        sqlite_executable += int(sqlite_ok)
        postgres_executable += int(postgres_ok)

        comparison: dict[str, Any]
        if not sqlite_ok:
            comparison = {
                "passed": False,
                "reason": "sqlite_execution_failed",
                "error": sqlite_result.get("error"),
            }
            difference_type = "other"
        elif not postgres_ok:
            comparison = {
                "passed": False,
                "reason": "postgres_execution_failed",
                "error": postgres_result.get("error"),
            }
            difference_type = "SQL translation"
        else:
            comparison = compare_case_results(
                case,
                list(sqlite_result.get("data") or []),
                list(postgres_result.get("data") or []),
            )
            difference_type = (
                "none" if comparison["passed"] else _difference_type(case, comparison)
            )
        passed = bool(comparison["passed"])
        parity_passed += int(passed)
        records.append(
            {
                "case_id": case_id,
                "sqlite_execution_ok": sqlite_ok,
                "postgres_execution_ok": postgres_ok,
                "sqlite_result": sqlite_result.get("data"),
                "postgres_result": postgres_result.get("data"),
                "parity_passed": passed,
                "difference_type": difference_type,
                "notes": comparison,
            }
        )

    return {
        "kind": "sqlite_postgresql_gold_parity",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "llm_called": False,
        "sqlite": sqlite_database_fingerprint(sqlite_path, tables=KEY_TABLES),
        "summary": {
            "business_cases": len(cases),
            "gold_sql_executable_sqlite": sqlite_executable,
            "gold_sql_executable_postgresql": postgres_executable,
            "ex_comparable_cases": comparable,
            "parity_passed": parity_passed,
            "parity_failed": comparable - parity_passed,
        },
        "cases": records,
    }


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("BI_DATABASE_URL"))
    parser.add_argument("--sqlite", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_url:
        print("Parity blocked: BI_DATABASE_URL is not configured", file=sys.stderr)
        return 2
    sqlite_path = get_sqlite_reference_path(args.sqlite)
    cases = apply_evaluation_overrides(load_business_cases(CASES), OVERRIDES)
    report = run_parity(str(args.database_url), sqlite_path, cases)
    output = args.output or RESULTS / (
        "postgres_parity_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {output}")
    return 0 if report["summary"]["parity_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
