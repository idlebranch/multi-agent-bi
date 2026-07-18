"""Run the real LLM workflow against every checked-in Olist golden question."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL  # noqa: E402
from src.graph import app as stable_graph  # noqa: E402
from src.graph_v2 import app_v2 as experimental_graph  # noqa: E402
from src.guardrails import sanitize_public_value, sanitize_result_rows  # noqa: E402
from src.state import create_initial_state  # noqa: E402
from src.tools.db_tools import get_db_path  # noqa: E402
from src.workflow import run_graph_once  # noqa: E402


GOLDEN_PATH = PROJECT_ROOT / "data" / "olist_golden_queries.json"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "live_batch_latest.json"


def values_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isclose(
            float(actual), float(expected), rel_tol=1e-7, abs_tol=0.02
        )
    if isinstance(expected, str) and isinstance(actual, (int, float)):
        try:
            return math.isclose(float(actual), float(expected), rel_tol=1e-7, abs_tol=0.02)
        except ValueError:
            return False
    return actual == expected


def row_contains_expected_values(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    remaining = list(actual.values())
    for expected_value in expected.values():
        for index, actual_value in enumerate(remaining):
            if values_match(actual_value, expected_value):
                remaining.pop(index)
                break
        else:
            return False
    return True


def result_matches_expected(
    rows: list[dict[str, Any]],
    expected: dict[str, Any] | None,
) -> bool:
    if expected is None:
        return True
    return any(row_contains_expected_values(row, expected) for row in rows)


def run_case(case: dict[str, Any], *, version: str, max_iterations: int) -> dict[str, Any]:
    graph = experimental_graph if version == "v2" else stable_graph
    started = time.perf_counter()
    state = create_initial_state(
        str(case["question"]),
        max_iterations=max_iterations,
        as_of_date="2018-10-17",
    )
    final_state, trace = run_graph_once(graph, state)
    elapsed = time.perf_counter() - started

    rows = final_state.get("sql_result", [])
    minimum_rows = int(case.get("min_rows", 1))
    stage_success = all(
        final_state.get(name) == "succeeded"
        for name in ("schema_status", "review_status", "validation_status", "execution_status")
    )
    semantic_match = result_matches_expected(rows, case.get("expected_first_row"))
    denied = [
        decision
        for decision in final_state.get("policy_decisions", [])
        if not decision.get("allowed", False)
    ]
    passed = (
        stage_success
        and len(rows) >= minimum_rows
        and semantic_match
        and not denied
    )

    return {
        "name": case["name"],
        "question": case["question"],
        "passed": passed,
        "stage_success": stage_success,
        "semantic_match": semantic_match,
        "minimum_rows": minimum_rows,
        "row_count": final_state.get("result_row_count", 0),
        "elapsed_seconds": round(elapsed, 3),
        "iteration": final_state.get("iteration", 0),
        "relevant_tables": final_state.get("relevant_tables", []),
        "sql": final_state.get("sql", ""),
        "result_preview": sanitize_result_rows(rows, for_llm=False, max_rows=5),
        "expected_reference": case.get("expected_first_row"),
        "review_issues": final_state.get("review_issues", []),
        "execution_error_code": final_state.get("execution_error_code", ""),
        "final_answer": final_state.get("final_answer", ""),
        "handoffs": [
            f"{event.get('from_agent')}->{event.get('to_agent')}"
            for event in final_state.get("handoff_history", [])
        ],
        "denied_policy_decisions": denied,
        "errors": final_state.get("error_history", []),
        "trace_nodes": [item.get("node") for item in trace],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=("v1", "v2"), default="v1")
    parser.add_argument("--limit", type=int, default=0, help="0 means all cases")
    parser.add_argument("--names", nargs="*", default=[], help="run only named cases")
    parser.add_argument("--max-iterations", type=int, default=12)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not DEEPSEEK_API_KEY:
        print("Live batch skipped: DEEPSEEK_API_KEY is not configured", file=sys.stderr)
        return 2

    cases = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    if args.names:
        selected_names = set(args.names)
        cases = [case for case in cases if case["name"] in selected_names]
    if args.limit > 0:
        cases = cases[: args.limit]

    results: list[dict[str, Any]] = []
    batch_started = time.perf_counter()
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['name']}: {case['question']}", flush=True)
        try:
            result = run_case(
                case,
                version=args.version,
                max_iterations=args.max_iterations,
            )
        except Exception as exc:
            result = {
                "name": case["name"],
                "question": case["question"],
                "passed": False,
                "error": sanitize_public_value(str(exc)),
            }
        results.append(sanitize_public_value(result, max_chars=100_000))
        print(
            f"  {'PASS' if result['passed'] else 'FAIL'} | "
            f"rows={result.get('row_count', 0)} | "
            f"semantic={result.get('semantic_match', False)} | "
            f"seconds={result.get('elapsed_seconds', 0)}",
            flush=True,
        )

    passed_count = sum(bool(result["passed"]) for result in results)
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "live_llm",
        "model": DEEPSEEK_MODEL,
        "workflow_version": args.version,
        "database": get_db_path().name,
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "pass_rate": round(passed_count / len(results), 4) if results else 0,
        "elapsed_seconds": round(time.perf_counter() - batch_started, 3),
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Completed: {passed_count}/{len(results)} passed; report={args.report}",
        flush=True,
    )
    return 0 if passed_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
