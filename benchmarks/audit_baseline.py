"""Re-evaluate a saved live run offline after evaluator-policy review."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.evaluators import classify_failure, evaluate_answer
from benchmarks.run_benchmark import (
    BUSINESS_CASES,
    EVALUATION_OVERRIDES,
    _execute,
    compare_case_results,
    database_fingerprint,
    markdown_summary,
    summarize,
)
from benchmarks.schema import apply_evaluation_overrides, load_business_cases
from src.tools.db_tools import get_db_path


MANUAL_FAILURE_REVIEW = {
    "B009": ("filter", "Agent used the non-existent status spelling 'cancelled' instead of 'canceled'."),
    "B034": ("wrong_join", "Joining consumer summary to order-level customers duplicated repeat consumers."),
    "B044": ("time_logic", "Agent grouped by delivery month while the benchmark convention uses purchase month."),
    "B050": ("reviewer_false_positive", "Reviewer contradicted the documented item_value and last-month semantics across repairs."),
    "B051": ("reviewer_false_positive", "Reviewer contradicted governed GMV and the workflow's documented recent-three-month window."),
}


def audit_business_result(
    case: dict[str, Any], raw: dict[str, Any], db_path: Path
) -> dict[str, Any]:
    result = dict(raw)
    result["pre_audit_execution_correct"] = raw.get("execution_correct")
    result["pre_audit_answer_correct"] = raw.get("answer_correct")
    result["pre_audit_final_passed"] = raw.get("final_passed")
    gold_rows: list[dict[str, Any]] = []
    execution_correct: bool | None = None
    comparison: dict[str, Any] | None = None

    if case["expected_behavior"] == "query":
        gold_execution = _execute(str(case["gold_sql"]), db_path)
        if not gold_execution["success"]:
            raise RuntimeError(f"gold SQL failed during audit: {case['case_id']}: {gold_execution['error']}")
        gold_rows = list(gold_execution.get("data") or [])
        if raw.get("sql_generated") and raw.get("sql_review_passed"):
            agent_execution = _execute(str(raw.get("sql") or ""), db_path)
            if agent_execution["success"]:
                comparison = compare_case_results(
                    case, gold_rows, list(agent_execution.get("data") or [])
                )
                execution_correct = bool(comparison["passed"])
            else:
                execution_correct = False
                comparison = {
                    "passed": False,
                    "reason": "agent_sql_execution_failed",
                    "error": agent_execution.get("error"),
                }
        else:
            execution_correct = False
            comparison = {"passed": False, "reason": "no_reviewed_sql"}

    answer_evaluation = evaluate_answer(
        str(raw.get("final_answer") or ""),
        case["answer_assertions"],
        gold_rows=gold_rows,
        response_status=str(raw.get("response_status") or ""),
    )
    answer_correct = bool(answer_evaluation["passed"])
    if case["expected_behavior"] == "query":
        behavior_correct = raw.get("response_status") in {"success", "no_data"}
        final_passed = bool(
            raw.get("transport_ok")
            and raw.get("workflow_completed")
            and behavior_correct
            and raw.get("sql_executed")
            and execution_correct
            and answer_correct
        )
    else:
        expected_status = case["answer_assertions"].get("expected_status")
        behavior_correct = (
            raw.get("response_status") == expected_status
            and not raw.get("sql_generated")
            and raw.get("execution_status") == "not_started"
        )
        final_passed = bool(
            raw.get("transport_ok")
            and raw.get("workflow_completed")
            and behavior_correct
            and answer_correct
        )

    failure_category = None
    failure_notes = ""
    if not final_passed:
        failure_category, failure_notes = classify_failure(
            case,
            raw,
            execution_correct=execution_correct,
            answer_correct=answer_correct,
            exception=str(raw.get("exception") or ""),
        )
        if case["case_id"] in MANUAL_FAILURE_REVIEW:
            failure_category, failure_notes = MANUAL_FAILURE_REVIEW[case["case_id"]]

    result.update(
        {
            "execution_correct": execution_correct,
            "answer_correct": answer_correct,
            "behavior_correct": behavior_correct,
            "final_passed": final_passed,
            "comparison": comparison,
            "answer_evaluation": answer_evaluation,
            "failure_category": failure_category,
            "failure_notes": failure_notes,
            "evaluation_override_applied": bool(case.get("evaluation_override_applied")),
            "audited_offline": True,
        }
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_report", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_path = args.raw_report.resolve()
    raw_report = json.loads(raw_path.read_text(encoding="utf-8"))
    cases = apply_evaluation_overrides(
        load_business_cases(BUSINESS_CASES), EVALUATION_OVERRIDES
    )
    by_id = {str(case["case_id"]): case for case in cases}
    db_path = get_db_path()
    database_before = database_fingerprint(db_path)
    audited_business = [
        audit_business_result(by_id[str(raw["case_id"])], raw, db_path)
        for raw in raw_report["business_results"]
    ]
    audited_safety = list(raw_report["safety_results"])
    database_after = database_fingerprint(db_path)
    timestamp = datetime.now(timezone.utc).isoformat()
    report = dict(raw_report)
    report["metadata"] = {
        **raw_report["metadata"],
        "audit_timestamp_utc": timestamp,
        "audit_of": str(raw_path),
        "audit_mode": "offline_saved_output_reevaluation",
        "answer_normalization": "NFKC, Chinese date/quarter, wan/yi units, governed entity aliases",
        "execution_normalization": "column permutation/projection, duplicate-preserving rows, numeric tolerance, explicit tie policies",
    }
    report["database_before"] = database_before
    report["database_after"] = database_after
    report["database_unchanged"] = database_before == database_after
    report["metrics"] = summarize(audited_business, audited_safety)
    report["business_results"] = audited_business
    report["safety_results"] = audited_safety
    report["raw_metrics_before_audit"] = raw_report.get("metrics", {})
    report["manual_failure_review"] = {
        case_id: {"category": value[0], "notes": value[1]}
        for case_id, value in MANUAL_FAILURE_REVIEW.items()
    }
    output = (
        args.output
        or raw_path.with_name(raw_path.stem + "_audited.json")
    ).resolve()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = output.with_suffix(".md")
    markdown_path.write_text(markdown_summary(report), encoding="utf-8")
    print(f"report={output}")
    print(f"summary={markdown_path}")
    return 0 if report["database_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
