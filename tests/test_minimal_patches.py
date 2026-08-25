from __future__ import annotations

import api

from src.contracts import SchemaSelection
from src.semantic_rules import review_sql_semantics


def test_compact_trace_preserves_long_list_types_and_timeline_counts():
    trace = [
        {
            "node": "sql_generation",
            "sql": "SELECT 1",
            "sql_attempt_history": [{"sql": "x" * 1300}, {"sql": "SELECT 2"}],
        },
        {
            "node": "sql_review",
            "review_status": "failed",
            "review_issues": [
                {"code": "other", "message": "x" * 1300} for _ in range(3)
            ],
        },
    ]

    compact = api._compact_trace(trace)
    assert isinstance(compact[0]["sql_attempt_history"], list)
    assert len(compact[0]["sql_attempt_history"]) == 2
    assert isinstance(compact[1]["review_issues"], list)
    assert len(compact[1]["review_issues"]) == 3

    timeline = api._build_timeline(compact, {})
    generation = next(item for item in timeline if item["node"] == "sql_generation")
    review = next(item for item in timeline if item["node"] == "sql_review")
    assert generation["summary"] == "已生成第 2 个 SQL 候选"
    assert review["summary"] == "发现 3 个问题，将按有限预算自动修复"


def test_non_governed_date_rule_is_applied_before_metric_return():
    issues = review_sql_semantics(
        "列出客户信息",
        "SELECT customer_id FROM customers WHERE created_at >= '2018-01-01'",
    )
    assert any(issue.code == "wrong_date_range" for issue in issues)


def test_schema_selection_defaults_are_backward_compatible():
    selection = SchemaSelection.model_validate(
        {"tables": ["orders"], "columns": {"orders": ["order_id"]}}
    )
    assert selection.question_status == "clear"
    assert selection.clarification_question == ""
