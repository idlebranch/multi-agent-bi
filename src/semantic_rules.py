"""Deterministic business-metric rules shared by catalog, writer, and reviewer."""

from __future__ import annotations

import json
import re
from datetime import date
from collections.abc import Iterable

from src.contracts import FilterNode, QueryPlan, ReviewIssue


MetricName = str


_GUIDANCE: dict[MetricName, str] = {
    "orders_by_status": (
        "Use the orders table, group by status, and count orders. Do not use a "
        "pre-aggregated GMV view for this metric."
    ),
    "monthly_delivered_gmv": (
        "Use product_sales, group purchase_timestamp by calendar month, filter "
        "order_status = 'delivered', and sum price as delivered_gmv. "
        "category_sales_summary cannot answer a monthly question because it has no date."
    ),
    "monthly_cancellation_rate": (
        "Use orders, group purchase_timestamp by calendar month, and calculate "
        "100.0 * canceled orders / all orders. Return a percentage on the 0-100 "
        "scale rather than a fraction on the 0-1 scale."
    ),
    "top_category_gmv": (
        "This is delivered GMV by English product category. Use "
        "category_sales_summary only for an all-time result; use product_sales when "
        "a time dimension or filter is requested. category_name is already English. Never join "
        "product_sales.category_name to category_translations.category_name because "
        "the former is English and the latter is Portuguese. product_sales.price is "
        "the item price excluding freight and SUM(price) is the governed GMV formula."
    ),
    "category_average_order_value": (
        "For an all-time category result use category_sales_summary. Its order_count "
        "is COUNT(DISTINCT delivered order_id), so the governed formula is "
        "delivered_gmv / order_count. For a time breakdown use product_sales and "
        "COUNT(DISTINCT order_id)."
    ),
    "on_time_delivery_rate": (
        "Return a percentage on the 0-100 scale. Use the single-row delivery_kpis "
        "only for an overall result. Use order_delivery_metrics for customer-state "
        "or time breakdowns. "
        "order_delivery_metrics is already limited to delivered orders; if it is "
        "used instead, calculate AVG(delivered_on_time) * 100. Do not add a date "
        "range unless the user explicitly requested one."
    ),
    "average_order_value": (
        "Prefer order_financials, which has one row per order. For delivered average "
        "order value, filter status = 'delivered' and calculate "
        "SUM(item_value) / COUNT(*). Category-level summaries are not valid because "
        "one order can contain multiple categories."
    ),
    "payment_value_by_type": (
        "Payment value defaults to all payment records, regardless of order status. "
        "Prefer payment_type_summary and select payment_type plus payment_value. "
        "Do not join orders or add a delivered/status filter unless the user "
        "explicitly requested that scope."
    ),
    "repeat_customers": (
        "A repeat customer is a customer_unique_id with at least two orders across "
        "all statuses and all dates by default. Prefer customer_order_summary; for "
        "a count, use COUNT(*) with order_count >= 2. Do not add delivered or date "
        "filters unless the user explicitly requested them."
    ),
    "top_seller_state_gmv": (
        "Use product_sales, filter order_status = 'delivered', group by seller_state, "
        "sum price as delivered_gmv, order descending, and apply the requested limit. "
        "product_sales also contains customer_state for customer/seller state cross-analysis."
    ),
    "delivery_review_comparison": (
        "Join order_delivery_metrics to reviews by order_id. Exclude rows where "
        "delivered_on_time IS NULL before comparing on-time and late groups; unknown "
        "delivery outcomes must not be classified as late."
    ),
    "delivered_customer_gmv_percentile": (
        "Use customer_order_summary, keep delivered_order_count >= 1, and calculate "
        "the percentile from delivered_gmv. In PostgreSQL use CUME_DIST or an explicitly "
        "defined rank; NTILE(100) is not the 99th-percentile threshold."
    ),
    "undelivered": (
        "Use the orders table. An undelivered (未签收) order is one whose "
        "delivered_customer_timestamp IS NULL AND whose status is NOT IN "
        "('canceled', 'unavailable'). Do not use a bare status != 'delivered' filter, "
        "because it incorrectly includes canceled and unavailable orders. Do not use "
        "order_financials, which lacks delivered_customer_timestamp."
    ),
}


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def identify_metric(question: str) -> MetricName | None:
    """Identify only metrics whose business meaning is governed by this project."""
    value = question.casefold()
    if (
        _contains_any(value, ("评价", "评分", "review", "rating"))
        and _contains_any(value, ("按时", "超时", "on-time", "late delivery"))
    ):
        return "delivery_review_comparison"
    if (
        _contains_any(value, ("百分位", "percentile", "p99"))
        and _contains_any(value, ("消费者", "客户", "customer"))
        and _contains_any(value, ("gmv", "销售额", "成交额", "revenue"))
    ):
        return "delivered_customer_gmv_percentile"
    if (
        _contains_any(value, ("订单状态", "order status", "orders by status"))
        and not re.search(r"(?:不是|不为|不等于|非)\s*(delivered|canceled|unavailable)", value)
        and _contains_any(value, ("多少", "数量", "count", "how many"))
    ):
        return "orders_by_status"
    if (
        _contains_any(value, ("销售趋势", "销售走势", "sales trend", "revenue trend"))
        and _contains_any(value, ("月", "最近", "过去", "month", "recent", "last"))
    ):
        return "monthly_delivered_gmv"
    if (
        _contains_any(value, ("每月", "按月", "月度", "monthly", "by month"))
        and _contains_any(value, ("gmv", "销售额", "成交额", "revenue"))
    ):
        return "monthly_delivered_gmv"
    if (
        _contains_any(value, ("取消率", "cancellation rate", "cancel rate"))
        and _contains_any(value, ("月", "month"))
    ):
        return "monthly_cancellation_rate"
    if (
        _contains_any(value, ("类别", "品类", "category", "categories"))
        and _contains_any(
            value,
            ("客单价", "平均订单金额", "average order value", "average basket", "aov"),
        )
    ):
        return "category_average_order_value"
    if (
        _contains_any(value, ("类别", "品类", "category", "categories"))
        and _contains_any(value, ("gmv", "销售额", "成交额", "revenue"))
    ):
        return "top_category_gmv"
    if _contains_any(
        value,
        ("按时送达率", "准时送达率", "按时交付率", "on-time delivery", "on time delivery"),
    ):
        return "on_time_delivery_rate"
    if _contains_any(
        value,
        ("客单价", "平均订单金额", "average order value", "average basket", "aov"),
    ):
        return "average_order_value"
    if (
        _contains_any(value, ("支付方式", "付款方式", "payment type", "payment method"))
        and _contains_any(value, ("金额", "payment value", "amount", "value"))
    ):
        return "payment_value_by_type"
    if _contains_any(value, ("复购", "repeat customer", "repeat buyer")) or (
        _contains_any(value, ("消费者", "客户", "customer"))
        and bool(
            re.search(
                r"(?:两|二|2)\s*次\s*(?:及以上|以上|or\s+more)|"
                r"(?:at\s+least\s+two|two\s+or\s+more)\s+orders?",
                value,
            )
        )
    ):
        return "repeat_customers"
    if (
        _contains_any(value, ("卖家州", "卖家所在州", "seller state", "seller states"))
        and _contains_any(value, ("gmv", "销售额", "成交额", "revenue"))
    ):
        return "top_seller_state_gmv"
    if question_requests_undelivered_scope(question):
        return "undelivered"
    return None


UNDELIVERED_TERMS = (
    "未签收",
    "未送达",
    "尚未送达",
    "尚未签收",
    "没有送达",
    "没有签收",
    "not delivered",
    "undelivered",
)

_EXPLICIT_STATUS_CONDITION = re.compile(
    r"\bstatus\s*(?:!=|<>|=|==)\s*['\"]?[a-z_0-9]", re.IGNORECASE
)


def _has_explicit_status_condition(value: str) -> bool:
    """Detect an explicit SQL-style status filter the user typed verbatim."""
    return bool(_EXPLICIT_STATUS_CONDITION.search(value))


def question_requests_delivered_scope(question: str) -> bool:
    value = question.casefold()
    if _contains_any(value, UNDELIVERED_TERMS):
        return False
    return _contains_any(
        value,
        ("已签收", "已交付", "已送达", "delivered", "completed orders"),
    )


def question_requests_undelivered_scope(question: str) -> bool:
    """True when the user asks about the governed undelivered concept.

    An explicit SQL-style status condition (e.g. ``status != 'delivered'``)
    always wins over the default semantic interpretation, so it is excluded.
    """
    value = question.casefold()
    if not _contains_any(value, UNDELIVERED_TERMS):
        return False
    return not _has_explicit_status_condition(value)


def undelivered_metric_is_ambiguous(question: str) -> bool:
    """Undelivered scope with a vague product-overview wording needs clarification."""
    if not question_requests_undelivered_scope(question):
        return False
    value = question.casefold()
    if not _contains_any(value, ("情况", "状况", "概览", "overview", "situation", "summary")):
        return False
    if not _contains_any(value, ("商品", "产品", "product", "item")):
        return False
    clear_metric = _contains_any(
        value,
        (
            "订单数",
            "订单数量",
            "订单量",
            "件数",
            "数量",
            "金额",
            "销售额",
            "gmv",
            "单价",
            "价格",
            "count",
            "revenue",
            "value",
        ),
    )
    return not clear_metric


def question_requests_time_scope(question: str) -> bool:
    value = question.casefold()
    return bool(
        re.search(r"(?:19|20)\d{2}(?:年|[-/])?", value)
        or re.search(r"(?<![\d])\d{2}\s*年", value)
        or _contains_any(
            value,
            (
                "上月",
                "本月",
                "这个月",
                "去年",
                "今年",
                "季度",
                "最近",
                "过去",
                "日期",
                "期间",
                "每月",
                "按月",
                "每年",
                "按年",
                "last month",
                "this month",
                "last year",
                "this year",
                "quarter",
                "between ",
                "from ",
                "date range",
                "monthly",
                "by month",
                "yearly",
                "by year",
            ),
        )
    )


def question_uses_relative_time(question: str) -> bool:
    value = question.casefold()
    return _contains_any(
        value,
        (
            "最近",
            "过去",
            "上月",
            "本月",
            "去年",
            "今年",
            "recent",
            "last month",
            "past ",
            "this month",
            "last year",
            "this year",
        ),
    )


def question_requests_customer_state(question: str) -> bool:
    return _contains_any(
        question.casefold(),
        ("客户州", "消费者州", "customer state", "customer_state"),
    )


def question_requests_seller_state(question: str) -> bool:
    return _contains_any(
        question.casefold(),
        ("卖家州", "卖家所在州", "seller state", "seller_state"),
    )


_DATE_COVERAGE_FALLBACK = {"start": "2016-09-04", "end": "2018-10-17"}


def get_date_coverage() -> dict[str, str]:
    """Return the single source-of-truth dataset date coverage metadata."""
    try:
        from src.config import DEFAULT_SEMANTIC_MODEL

        payload = json.loads(DEFAULT_SEMANTIC_MODEL.read_text(encoding="utf-8"))
        coverage = payload.get("date_coverage")
        if (
            isinstance(coverage, dict)
            and coverage.get("start")
            and coverage.get("end")
        ):
            return {"start": str(coverage["start"]), "end": str(coverage["end"])}
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return dict(_DATE_COVERAGE_FALLBACK)


def _extract_years(question: str) -> list[int]:
    return [
        int(match.group(1))
        for match in re.finditer(r"(?<![\d])((?:19|20)\d{2})(?![\d])", question)
    ]


def question_time_range_entirely_before_start(question: str) -> bool:
    """True when every referenced year precedes the dataset start year."""
    years = _extract_years(question)
    if not years:
        return False
    start_year = date.fromisoformat(get_date_coverage()["start"]).year
    return max(years) < start_year


def partial_date_coverage_note(question: str) -> str:
    """Return a deterministic note when the question references a partial year."""
    coverage = get_date_coverage()
    start_year = date.fromisoformat(coverage["start"]).year
    end_year = date.fromisoformat(coverage["end"]).year
    years = set(_extract_years(question))
    notes = []
    if start_year in years:
        notes.append(f"{start_year} 年数据仅从 {coverage['start']} 开始。")
    if end_year in years:
        notes.append(f"数据截至 {coverage['end']}。")
    return " ".join(notes)


def get_metric_guidance(question: str) -> str:
    metric = identify_metric(question)
    if metric is None:
        return "No additional governed metric rule applies."
    return (
        "GOVERNED METRIC POLICY (authoritative; overrides generic heuristics):\n"
        f"Metric: {metric}\nRule: {_GUIDANCE[metric]}"
    )


def preferred_tables_for_question(question: str) -> list[str]:
    """Return a safe pre-aggregated view when it fully covers the requested scope."""
    metric = identify_metric(question)
    has_time_scope = question_requests_time_scope(question)
    wants_delivered = question_requests_delivered_scope(question)
    if metric == "orders_by_status":
        return ["orders"]
    if metric == "monthly_delivered_gmv":
        return ["product_sales"]
    if metric == "monthly_cancellation_rate":
        return ["orders"]
    if metric == "top_category_gmv" and wants_delivered:
        return ["product_sales"] if has_time_scope else ["category_sales_summary"]
    if metric == "category_average_order_value":
        return ["product_sales"] if has_time_scope else ["category_sales_summary"]
    if metric == "on_time_delivery_rate":
        if has_time_scope or question_requests_customer_state(question):
            return ["order_delivery_metrics"]
        return ["delivery_kpis"]
    if metric == "average_order_value":
        return ["order_financials"]
    if metric == "payment_value_by_type" and not wants_delivered and not has_time_scope:
        return ["payment_type_summary"]
    if metric == "repeat_customers" and not wants_delivered and not has_time_scope:
        return ["customer_order_summary"]
    if metric == "top_seller_state_gmv":
        return ["product_sales"]
    if metric == "delivery_review_comparison":
        return ["order_delivery_metrics", "reviews"]
    if metric == "delivered_customer_gmv_percentile":
        return ["customer_order_summary"]
    if metric == "undelivered":
        return ["orders"]
    return []


def _contains_status_filter(sql: str) -> bool:
    return bool(
        re.search(
            r"\b(?:[a-z_]\w*\.)?(?:order_)?status\s*(?:=|==|!=|<>|\bin\b|\blike\b)",
            sql.casefold(),
        )
    )


def _contains_delivered_filter(sql: str) -> bool:
    return bool(
        re.search(
            r"\b(?:[a-z_]\w*\.)?(?:order_)?status\s*(?:=|==|\bin\s*\()"
            r"[^;]{0,80}?['\"]delivered['\"]",
            sql.casefold(),
        )
    )


def _contains_date_filter(sql: str) -> bool:
    value = sql.casefold()
    date_field = (
        r"(?:purchase_timestamp|order_purchase_timestamp|purchase_date|order_date|"
        r"created_at|delivery_date|delivered_customer_timestamp|date\s*\([^)]*\))"
    )
    return bool(
        re.search(
            rf"\b(?:where|and)\b[\s\S]{{0,240}}?{date_field}\s*"
            rf"(?:=|<|>|\bbetween\b|\bin\b|\blike\b)",
            value,
        )
    )


def _parenthesis_depths(sql: str) -> list[int]:
    depths = [0] * len(sql)
    depth = 0
    quote = ""
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote:
            depths[index] = depth
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    depths[index + 1] = depth
                    index += 2
                    continue
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        depths[index] = depth
        index += 1
    return depths


def _window_is_filtered_at_same_level(sql: str) -> bool:
    value = sql.casefold()
    if "lag" not in value:
        return False
    depths = _parenthesis_depths(value)
    lag_positions = [match.start() for match in re.finditer(r"\blag\s*\(", value)]
    where_matches = list(re.finditer(r"\bwhere\b", value))
    for lag_position in lag_positions:
        for where_match in where_matches:
            if where_match.start() <= lag_position:
                continue
            if depths[where_match.start()] != depths[lag_position]:
                continue
            predicate = value[where_match.end() : where_match.end() + 240]
            if re.search(r"\b(?:month|year|date)\b|to_char\s*\(", predicate):
                return True
    return False


def _uses_category_average_formula(sql: str) -> bool:
    value = sql.casefold()
    return bool(
        "category_sales_summary" in value
        and re.search(r"delivered_gmv[\s\S]{0,80}/[\s\S]{0,80}order_count", value)
    )


def _uses_product_sales_delivered_gmv(sql: str) -> bool:
    value = sql.casefold()
    return bool(
        "product_sales" in value
        and _contains_delivered_filter(value)
        and re.search(r"\bsum\s*\(\s*(?:[a-z_]\w*\.)?price\s*\)", value)
    )


def _uses_explicit_previous_month_mom(question: str, sql: str) -> bool:
    requested = re.search(r"((?:19|20)\d{2})-(0[1-9]|1[0-2])", question)
    if not requested:
        return False
    year = int(requested.group(1))
    month = int(requested.group(2))
    current = date(year, month, 1)
    previous = date(year - 1, 12, 1) if month == 1 else date(year, month - 1, 1)
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    value = sql.casefold()
    return bool(
        previous.isoformat() in value
        and current.isoformat() in value
        and following.isoformat() in value
        and re.search(r"(?:prev|previous)[a-z_]*", value)
        and re.search(r"\([^)]*-\s*(?:prev|previous)[a-z_]*\)[\s\S]{0,80}/", value)
    )


def _issue(code: str, message: str) -> ReviewIssue:
    return ReviewIssue(code=code, severity="high", message=message)  # type: ignore[arg-type]


def review_sql_semantics(question: str, sql: str) -> list[ReviewIssue]:
    """Apply hard semantic checks that do not depend on an LLM judgment."""
    metric = identify_metric(question)
    value = sql.casefold()
    issues: list[ReviewIssue] = []
    if "category_sales_summary" in value and question_requests_time_scope(question):
        issues.append(
            _issue(
                "wrong_columns",
                "category_sales_summary has no time column; use product_sales for "
                "category metrics by year or month.",
            )
        )
    if "delivery_kpis" in value and (
        question_requests_time_scope(question)
        or question_requests_customer_state(question)
        or metric == "delivery_review_comparison"
    ):
        issues.append(
            _issue(
                "wrong_columns",
                "delivery_kpis is an overall single-row table and cannot provide the "
                "requested breakdown; use order_delivery_metrics.",
            )
        )
    if (
        question_requests_customer_state(question)
        and question_requests_seller_state(question)
        and (
            "customer_state" not in value
            or "seller_state" not in value
            or re.search(r"['\"](?:n/?a|unknown)['\"]\s+as\s+customer_state", value)
        )
    ):
        issues.append(
            _issue(
                "wrong_columns",
                "The SQL must group by real customer_state and seller_state values; "
                "product_sales contains both columns.",
            )
        )
    question_value = question.casefold()
    if _contains_any(question_value, ("环比", "month-over-month", "mom")) and (
        _window_is_filtered_at_same_level(value)
    ):
        issues.append(
            _issue(
                "wrong_date_range",
                "The month filter is applied in the same SELECT level as LAG, so the "
                "previous month is removed before the window is calculated. Compute "
                "LAG in a CTE and filter the requested month in an outer SELECT.",
            )
        )
    if metric == "delivery_review_comparison" and (
        "delivered_on_time" in value
        and not re.search(r"delivered_on_time\s+is\s+not\s+null", value)
    ):
        issues.append(
            _issue(
                "wrong_metric",
                "Exclude delivered_on_time IS NULL before comparing on-time and late "
                "orders; unknown outcomes are not late deliveries.",
            )
        )
    if metric == "delivered_customer_gmv_percentile":
        if re.search(r"\bntile\s*\(\s*100\s*\)", value):
            issues.append(
                _issue(
                    "wrong_metric",
                    "NTILE(100) creates buckets and does not calculate the requested "
                    "99th-percentile threshold; use CUME_DIST or an explicit rank.",
                )
            )
        if (
            "customer_order_summary" in value
            and "delivered_order_count" not in value
            and not re.search(r"delivered_gmv\s*>\s*0", value)
        ):
            issues.append(
                _issue(
                    "missing_status_filter",
                    "Keep only customers with delivered_order_count >= 1 before "
                    "calculating the delivered-GMV percentile.",
                )
            )

    if (
        metric == "category_average_order_value"
        and "category_sales_summary" in value
        and not _uses_category_average_formula(value)
    ):
        issues.append(
            _issue(
                "wrong_aggregation",
                "For category_sales_summary, average order value must be "
                "delivered_gmv / order_count; order_count is already a distinct "
                "delivered-order count per category.",
            )
        )
    if metric == "monthly_cancellation_rate" and not (
        re.search(r"\*\s*100(?:\.0+)?\b", value)
        or re.search(r"\b100(?:\.0+)?\s*\*", value)
    ):
        issues.append(
            _issue(
                "wrong_metric",
                "Monthly cancellation rate must be returned as a percentage on the "
                "0-100 scale; multiply the canceled-order fraction by 100.",
            )
        )
    if not question_requests_time_scope(question) and _contains_date_filter(value):
        issues.append(
            _issue(
                "wrong_date_range",
                "Remove the date filter because the user did not request a time range.",
            )
        )
    if metric is None:
        return issues

    if metric == "top_category_gmv":
        if "product_sales" in value and "category_translations" in value:
            issues.append(
                _issue(
                    "join_fanout",
                    "product_sales.category_name is already English and must not be "
                    "joined to the Portuguese category_translations.category_name.",
                )
            )
        if (
            "product_sales" in value
            and "category_sales_summary" not in value
            and not _contains_delivered_filter(value)
        ):
            issues.append(
                _issue(
                    "missing_status_filter",
                    "Delivered category GMV from product_sales requires "
                    "order_status = 'delivered'.",
                )
            )

    elif metric in {"monthly_delivered_gmv", "top_seller_state_gmv"}:
        if "product_sales" in value and not _contains_delivered_filter(value):
            issues.append(
                _issue(
                    "missing_status_filter",
                    "Delivered GMV from product_sales requires "
                    "order_status = 'delivered'.",
                )
            )

    elif metric == "on_time_delivery_rate":
        uses_percentage_view = (
            "delivery_kpis" in value and "on_time_delivery_pct" in value
        )
        average_match = re.search(r"avg\s*\([^)]*delivered_on_time[^)]*\)", value)
        scaled_average = False
        if average_match:
            before = value[max(0, average_match.start() - 20) : average_match.start()]
            after = value[average_match.end() : average_match.end() + 20]
            scaled_average = bool(
                re.search(r"100(?:\.0+)?\s*\*\s*$", before)
                or re.match(r"\s*\*\s*100(?:\.0+)?\b", after)
            )
        if not uses_percentage_view and not scaled_average:
            issues.append(
                _issue(
                    "wrong_metric",
                    "On-time delivery rate must be returned as a 0-100 percentage: "
                    "select delivery_kpis.on_time_delivery_pct or multiply "
                    "AVG(delivered_on_time) by 100.",
                )
            )

    elif metric == "payment_value_by_type":
        if not question_requests_delivered_scope(question) and _contains_status_filter(value):
            issues.append(
                _issue(
                    "wrong_metric",
                    "The question requests all payments; remove the unrequested order "
                    "status filter or use payment_type_summary.",
                )
            )

    elif metric == "repeat_customers":
        if not question_requests_delivered_scope(question) and _contains_status_filter(value):
            issues.append(
                _issue(
                    "wrong_metric",
                    "Repeat customers default to all orders; remove the unrequested "
                    "order status filter.",
                )
            )
        if _contains_any(question.casefold(), ("多少", "how many", "count")) and not re.search(
            r"\bcount\s*\(", value
        ):
            issues.append(
                _issue(
                    "wrong_aggregation",
                    "The question asks for one repeat-customer count, not one row per customer.",
                )
            )

    elif metric == "undelivered":
        if (
            re.search(r"\b(?:order_)?status\s*(?:!=|<>)\s*['\"]?delivered['\"]?", value)
            and "delivered_customer_timestamp is null" not in value
        ):
            issues.append(
                _issue(
                    "wrong_metric",
                    "未签收（undelivered）的 governed 定义是 "
                    "delivered_customer_timestamp IS NULL 且 "
                    "status NOT IN ('canceled','unavailable')；不要使用 "
                    "status != 'delivered'，否则会把 canceled/unavailable 错误计入。",
                )
            )

    return issues


def _status_filter_is_not_required(metric: MetricName, sql: str, question: str) -> bool:
    value = sql.casefold()
    if metric in {"on_time_delivery_rate", "delivery_review_comparison"}:
        return "delivery_kpis" in value or "order_delivery_metrics" in value
    if metric in {"payment_value_by_type", "repeat_customers"}:
        return not question_requests_delivered_scope(question)
    if metric == "undelivered":
        return "delivered_customer_timestamp is null" in value
    return False


def reconcile_llm_issues(
    question: str,
    sql: str,
    issues: Iterable[ReviewIssue],
) -> list[ReviewIssue]:
    """Discard reviewer findings that directly contradict governed metric policy."""
    metric = identify_metric(question)
    if metric is None:
        return list(issues)

    reconciled: list[ReviewIssue] = []
    for issue in issues:
        message = issue.message.casefold()
        if (
            issue.code in {"wrong_date_range", "wrong_metric", "other"}
            and _contains_any(question.casefold(), ("环比", "month-over-month", "mom"))
            and _uses_explicit_previous_month_mom(question, sql)
        ):
            continue
        if (
            metric == "category_average_order_value"
            and issue.code in {"wrong_aggregation", "wrong_metric"}
            and _uses_category_average_formula(sql)
        ):
            continue
        if (
            metric
            in {"top_category_gmv", "monthly_delivered_gmv", "top_seller_state_gmv"}
            and issue.code in {"wrong_metric", "other"}
            and _uses_product_sales_delivered_gmv(sql)
            and _contains_any(message, ("price", "freight", "运费"))
            and not _contains_any(message, ("join", "group", "window", "连接", "分组"))
        ):
            continue
        if issue.code == "missing_status_filter" and _status_filter_is_not_required(
            metric, sql, question
        ):
            continue
        if (
            issue.code == "wrong_date_range"
            and not question_requests_time_scope(question)
            and not _contains_date_filter(sql)
        ):
            continue
        # Some providers occasionally use a generic code for a single status/date
        # complaint. Reconcile only narrow messages so real aggregation errors survive.
        if issue.code in {"wrong_metric", "other"}:
            status_words = ("status", "delivered", "状态", "签收", "交付")
            other_problem_words = (
                "count",
                "multiple rows",
                "aggregation",
                "join",
                "percentage",
                "percent",
                "多行",
                "计数",
                "聚合",
                "连接",
                "百分比",
            )
            if (
                _contains_any(message, status_words)
                and not _contains_any(message, other_problem_words)
                and _status_filter_is_not_required(metric, sql, question)
            ):
                continue
            date_words = ("date", "time range", "as-of", "month", "日期", "时间范围", "月份")
            if (
                _contains_any(message, date_words)
                and not _contains_any(message, other_problem_words)
                and not question_requests_time_scope(question)
                and not _contains_date_filter(sql)
            ):
                continue
        reconciled.append(issue)
    return reconciled


def _filter_leaves(node: FilterNode) -> Iterable[FilterNode]:
    if node.children:
        for child in node.children:
            yield from _filter_leaves(child)
    else:
        yield node


def check_plan_consistency(plan: QueryPlan, sql: str) -> list[ReviewIssue]:
    """Deterministic check that the SQL preserves the governed query plan.

    Verifies that filter values (statuses, states, thresholds) and null-check
    fields from the query plan still appear in the generated SQL, so the
    reviewer can deterministically reject SQL that dropped a requested filter.
    """
    if plan is None or plan.filter_tree is None:
        return []
    value = sql.casefold()
    issues: list[ReviewIssue] = []
    for leaf in _filter_leaves(plan.filter_tree):
        # A "delivered" scope is frequently enforced by a governed semantic view
        # rather than an explicit WHERE clause; metric rules cover that case.
        if leaf.field == "status" and leaf.value == "delivered":
            continue
        if leaf.op in {"is_null", "is_not_null"} and leaf.field:
            if leaf.field.casefold() not in value:
                issues.append(
                    _issue(
                        "missing_status_filter",
                        f"query plan requires {leaf.field} IS NULL but SQL is missing it",
                    )
                )
            continue
        if leaf.value is None:
            continue
        candidates = leaf.value if isinstance(leaf.value, list) else [leaf.value]
        for item in candidates:
            if item is None:
                continue
            token = str(item).casefold().strip()
            if token and token not in value:
                issues.append(
                    _issue(
                        "missing_status_filter",
                        f"query plan filter value {item!r} is missing from the SQL",
                    )
                )
    return issues
