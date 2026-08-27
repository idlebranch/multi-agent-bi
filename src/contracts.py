"""Typed contracts exchanged between BI agents and the orchestrator."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentName = Literal[
    "start",
    "schema_linking",
    "sql_generation",
    "sql_review",
    "sql_validation",
    "sql_execution",
    "format_answer",
]

ReviewIssueCode = Literal[
    "join_fanout",
    "missing_status_filter",
    "wrong_metric",
    "wrong_date_range",
    "wrong_aggregation",
    "wrong_columns",
    "unsafe_sql",
    "ambiguous_intent",
    "unanswerable",
    "other",
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SchemaSelection(StrictContract):
    tables: list[str] = Field(default_factory=list, max_length=20)
    columns: dict[str, list[str]] = Field(default_factory=dict)
    reasoning: str = Field(default="", max_length=1000)
    question_status: Literal["clear", "ambiguous"] = "clear"
    clarification_question: str = Field(default="", max_length=500)


class ReviewIssue(StrictContract):
    code: ReviewIssueCode
    severity: Literal["low", "medium", "high"]
    message: str = Field(min_length=1, max_length=500)


class SQLReviewResult(StrictContract):
    approved: bool
    summary: str = Field(default="", max_length=500)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def decision_matches_issues(self) -> SQLReviewResult:
        if not self.approved and not self.issues:
            raise ValueError("a rejected SQL review must include at least one issue")
        if self.approved and any(issue.severity == "high" for issue in self.issues):
            raise ValueError("approved SQL cannot contain a high-severity issue")
        return self


class HandoffEvent(StrictContract):
    run_id: str = Field(min_length=8, max_length=64)
    from_agent: AgentName
    to_agent: AgentName
    reason_code: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=1000)
    attempt: int = Field(ge=1)
    policy_version: str
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PolicyDecision(StrictContract):
    policy_version: str
    action: str = Field(min_length=1, max_length=200)
    allowed: bool
    reason: str = Field(min_length=1, max_length=1000)
    agent: AgentName
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class SQLAttempt(StrictContract):
    attempt: int = Field(ge=1)
    sql: str = Field(min_length=1, max_length=100_000)
    trigger_error: str = Field(default="", max_length=2000)
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class FilterNode(StrictContract):
    """A single resolved filter condition or a nested AND/OR/NOT group."""

    op: str
    field: str | None = None
    value: Any | None = None
    children: list["FilterNode"] = Field(default_factory=list)


class StructuredIntent(StrictContract):
    """Deterministic business-intent slots extracted from the user question."""

    entity: str | None = None
    metric: str | None = None
    aggregation: str | None = None
    counting_unit: str | None = None
    time_range: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    filters: FilterNode | None = None
    group_by: list[str] = Field(default_factory=list)
    sort_field: str | None = None
    sort_order: str | None = None
    limit: int | None = None
    comparison: str | None = None
    analysis_type: str = "summary"
    business_concepts: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    confidence: str = "high"
    semantic_coverage: str = "low"


class QueryPlan(StrictContract):
    """Governed intermediate representation between intent and SQL."""

    selected_tables: list[str] = Field(default_factory=list)
    selected_columns: dict[str, list[str]] = Field(default_factory=dict)
    metric_expression: str | None = None
    filter_tree: FilterNode | None = None
    group_by: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    time_scope: str | None = None
    time_field: str = "purchase_timestamp"
    time_grain: str | None = None
    time_boundaries: dict | None = None
    governed_rules_applied: list[str] = Field(default_factory=list)
    analysis_type: str = "summary"


class AnalysisResult(StrictContract):
    """Deterministic post-query analysis facts, not LLM prose."""

    analysis_type: str = "summary"
    summary: str = ""
    facts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
