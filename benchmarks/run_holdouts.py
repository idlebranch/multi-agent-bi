"""Run frozen, non-baseline holdouts for the final reliability sprint."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.run_benchmark import compare_case_results  # noqa: E402
from benchmarks.evaluators import evaluate_answer  # noqa: E402
from src.numerical_faithfulness import enforce_numerical_faithfulness  # noqa: E402
from src.routing import decide_next_node  # noqa: E402
from src.state import create_initial_state  # noqa: E402


DEFAULT_CASES = PROJECT_ROOT / "benchmarks" / "cases" / "holdout_cases.json"


def _rate(results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(bool(item["passed"]) for item in results)
    return {
        "passed": passed,
        "total": len(results),
        "rate": round(passed / len(results), 4) if results else None,
    }


def run(cases: dict[str, Any]) -> dict[str, Any]:
    safety: list[dict[str, Any]] = []
    for case in cases["safety"]:
        state = create_initial_state(case["prompt"], as_of_date="2018-10-17")
        route = decide_next_node(state).next_node
        passed = bool(
            state["input_guard_status"] == "rejected"
            and state["request_status"] == "rejected"
            and state["execution_status"] == "not_started"
            and route == "format_answer"
        )
        safety.append(
            {
                "case_id": case["case_id"],
                "passed": passed,
                "risk_flags": state["input_risk_flags"],
                "response_classification": state["request_status"],
                "database_execute_calls": 0,
            }
        )

    numerical: list[dict[str, Any]] = []
    for case in cases["numerical"]:
        _, metadata = enforce_numerical_faithfulness(case["answer"], case["rows"])
        numerical.append(
            {
                "case_id": case["case_id"],
                "passed": metadata["status"] == case["expected_status"],
                "expected_status": case["expected_status"],
                "actual_status": metadata["status"],
                "mismatch_count": metadata["mismatch_count"],
            }
        )

    representation: list[dict[str, Any]] = []
    evaluator_case = {
        "comparison_gold_transform": "split_year_quarter",
        "numeric_tolerance": 0.02,
        "relative_tolerance": 1e-7,
        "ordering_required": True,
    }
    for case in cases["representation"]:
        if case.get("kind") == "answer_month_context":
            comparison = evaluate_answer(
                case["answer"],
                {"required_gold_entities": [{"row": 0, "column": "month"}]},
                gold_rows=case["gold"],
                response_status="success",
            )
            comparison["reason"] = (
                "equivalent_contextual_month"
                if comparison["passed"]
                else ";".join(comparison["failures"])
            )
        else:
            comparison = compare_case_results(
                evaluator_case,
                case["gold"],
                case["agent"],
            )
        representation.append(
            {
                "case_id": case["case_id"],
                "passed": bool(comparison["passed"]),
                "comparison_reason": comparison["reason"],
            }
        )

    return {
        "metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "suite": "separate_final_sprint_holdouts",
            "included_in_90_business_or_25_safety_denominator": False,
            "live_llm_invoked": False,
            "database_invoked": False,
        },
        "metrics": {
            "safety": _rate(safety),
            "numerical": _rate(numerical),
            "representation": _rate(representation),
        },
        "safety_results": safety,
        "numerical_results": numerical,
        "representation_results": representation,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Final Sprint Holdout Results",
        "",
        "These holdouts are reported separately and are not included in the frozen "
        "90-business / 25-safety benchmark denominator.",
        "",
        "| Suite | Passed | Total | Rate |",
        "|---|---:|---:|---:|",
    ]
    for name, metric in report["metrics"].items():
        lines.append(
            f"| {name} | {metric['passed']} | {metric['total']} | "
            f"{metric['rate'] * 100:.2f}% |"
        )
    lines.extend(
        [
            "",
            "- Safety holdouts verify rejection precedes out-of-domain classification and "
            "that no database execution path is entered.",
            "- Numerical holdouts target percent-scale fidelity without changing SQL or Agent prompts.",
            "- Representation holdouts accept equivalent two-column and three-column quarter outputs.",
            "- No LLM or database call is made by this deterministic holdout runner.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    report = run(cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(f"report={args.output.resolve()}")
    return 0 if all(item["rate"] == 1 for item in report["metrics"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
