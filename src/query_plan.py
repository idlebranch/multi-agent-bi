"""Governed query-plan builder between structured intent and SQL.

The query plan is the authoritative intermediate representation: it resolves
the intent to concrete tables, columns, a metric expression, a filter tree, and
grouping/ordering. The SQL writer consumes this plan rather than re-deriving
everything from the raw question.
"""

from __future__ import annotations

import re

from src.contracts import FilterNode, QueryPlan, StructuredIntent
from src.semantic_rules import preferred_tables_for_question

_METRIC_EXPRESSION = {
    "undelivered": "COUNT(*)",
    "orders_by_status": "COUNT(*)",
    "monthly_cancellation_rate": "100.0 * SUM(CASE WHEN status='canceled' THEN 1 ELSE 0 END) / COUNT(*)",
    "repeat_customers": "COUNT(*)",
    "delivered_customer_gmv_percentile": "MIN(delivered_gmv)",
    "average_order_value": "SUM(item_value) / COUNT(*)",
    "category_average_order_value": "delivered_gmv / order_count",
    "monthly_delivered_gmv": "SUM(price)",
    "top_category_gmv": "SUM(price)",
    "top_seller_state_gmv": "SUM(price)",
    "payment_value_by_type": "SUM(payment_value)",
}


def _collect_filter_fields(node: FilterNode | None) -> set[str]:
    if node is None:
        return set()
    if node.field:
        return {node.field}
    return {field for child in node.children for field in _collect_filter_fields(child)}


def _has_undelivered_filter(node: FilterNode | None) -> bool:
    if node is None:
        return False
    if node.op == "undelivered":
        return True
    return any(_has_undelivered_filter(child) for child in node.children)


def _expand_governed_filters(node: FilterNode | None) -> FilterNode | None:
    """Expand governed concepts into concrete, SQL-ready filter conditions."""
    if node is None:
        return None
    if node.op == "undelivered":
        return FilterNode(
            op="and",
            children=[
                FilterNode(op="is_null", field="delivered_customer_timestamp"),
                FilterNode(
                    op="not_in", field="status", value=["canceled", "unavailable"]
                ),
            ],
        )
    if node.children:
        return FilterNode(
            op=node.op, children=[_expand_governed_filters(c) for c in node.children]
        )
    return node


def _metric_tables(intent: StructuredIntent, question: str) -> list[str]:
    governed = preferred_tables_for_question(question)
    if governed:
        return governed
    return []


def _resolve_tables(intent: StructuredIntent, question: str) -> list[str]:
    if intent.metric:
        tables = _metric_tables(intent, question)
        if tables:
            return tables
    fields = _collect_filter_fields(intent.filters)
    group_by = set(intent.group_by)
    counting_unit = intent.counting_unit
    if _has_undelivered_filter(intent.filters):
        # undelivered requires delivered_customer_timestamp, only present in orders.
        return ["orders"]
    # Only clear, governed mappings stay deterministic.
    if "payment_type" in group_by:
        return ["payment_type_summary"]
    if counting_unit == "order":
        if fields.intersection({"customer_state", "item_value", "payment_value"}) or "customer_state" in group_by:
            return ["order_financials"]
        return ["orders"]
    # value/item/distinct_product grains are ambiguous; leave to legacy Schema Linking.
    return []


def resolve_tables(intent: StructuredIntent, question: str) -> list[str]:
    """Deterministic table selection for a structured intent."""
    return _resolve_tables(intent, question)


def _metric_expression(intent: StructuredIntent) -> str | None:
    if intent.metric in _METRIC_EXPRESSION:
        return _METRIC_EXPRESSION[intent.metric]
    counting_unit = intent.counting_unit
    if counting_unit == "order":
        return "COUNT(*)"
    if counting_unit == "item":
        return "COUNT(*)"
    if counting_unit == "distinct_product":
        return "COUNT(DISTINCT product_id)"
    if counting_unit == "value":
        return "SUM(price)"
    return None


def _column_map(intent: StructuredIntent, tables: list[str]) -> dict[str, list[str]]:
    group_by = intent.group_by
    fields = _collect_filter_fields(intent.filters)
    columns: dict[str, list[str]] = {}
    for table in tables:
        cols: set[str] = set()
        for dimension in group_by:
            if dimension == "month":
                cols.add("purchase_timestamp")
            elif dimension == "customer_state":
                cols.add("customer_state")
            elif dimension == "seller_state":
                cols.add("seller_state")
            elif dimension == "category_name":
                cols.add("category_name")
            elif dimension == "payment_type":
                cols.add("payment_type")
            elif dimension == "status":
                cols.add("status")
        cols.update(fields)
        columns[table] = sorted(cols)
    return columns


def _order_by(intent: StructuredIntent, metric_expression: str | None) -> list[str]:
    if not intent.sort_order:
        return []
    direction = "DESC" if intent.sort_order == "desc" else "ASC"
    if intent.sort_field == "metric" and metric_expression:
        return [f"{metric_expression} {direction}"]
    if intent.sort_field and intent.sort_field != "metric":
        return [f"{intent.sort_field} {direction}"]
    if intent.metric:
        return [f"{intent.metric} {direction}"]
    return []


def _time_grain(intent: StructuredIntent) -> str | None:
    for dim in ("month", "quarter", "year"):
        if dim in intent.group_by:
            return dim
    return None


def _time_boundaries(time_scope: str | None) -> dict | None:
    """Convert a time_scope token into a half-open [start, end) boundary pair."""
    if not time_scope:
        return None
    m = re.fullmatch(r"(20\d{2})", time_scope)
    if m:
        year = int(m.group(1))
        return {"start": f"{year}-01-01", "end": f"{year + 1}-01-01"}
    m = re.fullmatch(r"(20\d{2})-Q([1-4])", time_scope)
    if m:
        year, quarter = int(m.group(1)), int(m.group(2))
        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 3
        end_year = year + 1 if end_month > 12 else year
        end_month = 1 if end_month > 12 else end_month
        return {"start": f"{year}-{start_month:02d}-01", "end": f"{end_year}-{end_month:02d}-01"}
    m = re.fullmatch(r"(20\d{2})-H([12])", time_scope)
    if m:
        year, half = int(m.group(1)), int(m.group(2))
        if half == 1:
            return {"start": f"{year}-01-01", "end": f"{year}-07-01"}
        return {"start": f"{year}-07-01", "end": f"{year + 1}-01-01"}
    m = re.fullmatch(r"(20\d{2})-(\d{2})", time_scope)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        end_year = year + 1 if month == 12 else year
        end_month = 1 if month == 12 else month + 1
        return {"start": f"{year}-{month:02d}-01", "end": f"{end_year}-{end_month:02d}-01"}
    return None


def build_query_plan(
    intent: StructuredIntent,
    question: str,
    relevant_tables: list[str],
    relevant_columns: dict[str, list[str]],
) -> QueryPlan:
    tables = list(relevant_tables) or _resolve_tables(intent, question)
    metric_expression = _metric_expression(intent)
    filter_tree = _expand_governed_filters(intent.filters)
    governed_rules = [concept for concept in intent.business_concepts]
    if intent.metric:
        governed_rules.append(intent.metric)

    selected_columns = dict(relevant_columns)
    if not selected_columns:
        selected_columns = _column_map(intent, tables)

    return QueryPlan(
        selected_tables=tables,
        selected_columns=selected_columns,
        metric_expression=metric_expression,
        filter_tree=filter_tree,
        group_by=list(intent.group_by),
        order_by=_order_by(intent, metric_expression),
        limit=intent.limit,
        time_scope=intent.time_range,
        time_grain=_time_grain(intent),
        time_boundaries=_time_boundaries(intent.time_range),
        governed_rules_applied=governed_rules,
        analysis_type=intent.analysis_type,
    )


def plan_to_prompt_block(plan: QueryPlan) -> str:
    """Render the query plan as a compact, deterministic prompt fragment."""
    import json

    return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
