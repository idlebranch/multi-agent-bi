"""Deterministic structured business-intent extraction and clarification gate.

This module converts a natural-language question into a structured
``StructuredIntent`` before any SQL is written. It prefers deterministic,
reusable rules (temporal expressions, dimension/entity synonyms, operators,
negation, counting-unit roles, ranking expressions, analysis intent) over
LLM guessing so that equivalent phrasings resolve to the same concept.
"""

from __future__ import annotations

import re
from typing import Any

from src.contracts import FilterNode, StructuredIntent
from src.semantic_rules import identify_metric

_STATE_CODES = {
    "SP", "RJ", "MG", "RS", "PR", "SC", "BA", "PE", "GO", "DF", "CE", "ES",
    "AM", "PA", "MT", "MS", "MA", "PB", "RN", "AL", "SE", "PI", "RO", "TO",
    "AC", "AP", "RR",
}

_STATE_ALIASES = {
    "圣保罗": "SP",
    "里约热内卢": "RJ",
    "里约": "RJ",
}

_CN_NUMERALS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_UNDELIVERED_TERMS = (
    "未签收", "未送达", "尚未送达", "尚未签收", "没有送达", "没有签收",
    "还没签收", "还没送达", "还没送到", "没送到", "没签收", "没送达",
    "not delivered", "undelivered",
)

_OR_SPLIT = re.compile(r"或者|或")
_TIME_YEAR = re.compile(r"(?<![\d])((?:19|20)\d{2})(?![\d])")
_TWO_DIGIT_YEAR = re.compile(r"(?<![0-9])([0-9]{2})\s*年")
_STATE_CODE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2})(?![A-Za-z0-9])")
_VALUE_GT = re.compile(
    r"(商品金额|订单商品金额|金额|商品价格|单价|价格)\s*(?:超过|大于|高于|以上|至少|多于)\s*(\d+(?:\.\d+)?)"
)
_VALUE_LT = re.compile(
    r"(商品金额|订单商品金额|金额|商品价格|单价|价格)\s*(?:低于|小于|以下|不足|少于)\s*(\d+(?:\.\d+)?)"
)
_VALUE_COMPARATOR = re.compile(
    r"(金额|价格|gmv|amount|price|value)\s*(?:超过|大于|高于|多于|至少|少于|低于|小于|不足|不超过|不少于)"
)
_STATUS_NEGATION = re.compile(
    r"(?:不是|不为|不等于|非)\s*(delivered|canceled|unavailable|已签收|已交付|已送达|取消)"
)


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def _cn_numeral_to_int(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token in _CN_NUMERALS:
        return _CN_NUMERALS[token]
    if token.startswith("十") and len(token) == 2 and token[1] in _CN_NUMERALS:
        return 10 + _CN_NUMERALS[token[1]]
    if len(token) == 2 and token[1] == "十" and token[0] in _CN_NUMERALS:
        return _CN_NUMERALS[token[0]] * 10
    return None


def _has_value_comparator(value: str) -> bool:
    return bool(_VALUE_COMPARATOR.search(value))


def _map_status(token: str) -> str | None:
    t = token.casefold()
    if t in ("delivered", "已签收", "已交付", "已送达"):
        return "delivered"
    if t in ("canceled", "cancelled", "取消"):
        return "canceled"
    if t in ("unavailable", "不可用"):
        return "unavailable"
    return None


def _value_filter_field(term: str) -> str:
    return "price" if term in ("商品价格", "单价", "价格") else "item_value"


def _extract_time_range(question: str) -> str | None:
    q = question.casefold()
    year = None
    match = _TIME_YEAR.search(q)
    if match:
        year = match.group(1)
    else:
        match = _TWO_DIGIT_YEAR.search(q)
        if match:
            yy = int(match.group(1))
            year = str(2000 + yy if yy < 50 else 1900 + yy)
    if year is None:
        return None

    quarter = None
    m = re.search(r"q\s*([1-4])", q)
    if m:
        quarter = m.group(1)
    else:
        for name, num in (("一季度", "1"), ("二季度", "2"), ("三季度", "3"), ("四季度", "4")):
            if name in q:
                quarter = num
                break
        if quarter is None:
            m = re.search(r"第?\s*([1-4])\s*季度", q)
            if m:
                quarter = m.group(1)
    if quarter:
        return f"{year}-Q{quarter}"
    if "上半年" in q or "前六个月" in q or "前6个月" in q:
        return f"{year}-H1"
    if "下半年" in q:
        return f"{year}-H2"
    month = re.search(r"(?<![\d])([1-9]|1[0-2])\s*月", q)
    if month:
        return f"{year}-{int(month.group(1)):02d}"
    return year


def _extract_counting_unit(question: str, metric: str | None) -> str | None:
    q = question.casefold()
    if metric in {
        "delivered_gmv", "monthly_delivered_gmv", "top_category_gmv",
        "top_seller_state_gmv", "average_order_value", "category_average_order_value",
        "delivered_customer_gmv_percentile",
    }:
        return "value"
    if metric in {"on_time_delivery_rate", "monthly_cancellation_rate"}:
        return "percentage"
    if metric == "undelivered":
        return "order"
    if _contains_any(q, ("订单数", "订单数量", "订单量", "多少订单", "order count", "number of orders", "多少个订单", "多少单", "几单", "订单有多少", "有多少订单")):
        return "order"
    if _contains_any(q, ("件数", "商品件数", "销量", "销售量", "item count")):
        return "item"
    if _contains_any(q, ("商品数", "distinct product", "哪些商品", "商品有哪些")):
        return "distinct_product"
    # "X 分布/占比/构成/各占" -> count of the entity.
    if _contains_any(q, ("分布", "占比", "构成", "各占", "比例")):
        return "order"
    # Generic counting phrase resolved by the entity noun.
    if _contains_any(q, ("多少", "几", "数量", "个数", "个", "份", "count", "数")):
        if _contains_any(q, ("订单", "order", "单")):
            return "order"
        if _contains_any(q, ("商品", "产品", "product")):
            return "item"
    # Value metric only when "金额/价格" is NOT part of a filter comparator.
    if _contains_any(q, ("金额", "销售额", "收入", "营业额", "gmv", "revenue", "sales", "客单价", "单价", "价格")) and not _has_value_comparator(q):
        return "value"
    if _contains_any(q, ("订单", "order")) and _contains_any(q, ("最多", "最少", "数量", "多少", "前", "排名", "数", "top", "大")):
        return "order"
    return None


def _infer_aggregation(metric: str | None, counting_unit: str | None) -> str | None:
    if metric in {"average_order_value", "category_average_order_value", "on_time_delivery_rate"}:
        return "avg"
    if counting_unit == "distinct_product":
        return "count_distinct"
    if counting_unit in {"order", "item"}:
        return "count"
    if counting_unit == "value":
        return "sum"
    return "count"


def _extract_entity(question: str, metric: str | None) -> str:
    q = question.casefold()
    if metric in {"delivered_customer_gmv_percentile", "repeat_customers"}:
        return "customer"
    if _contains_any(q, ("消费者", "客户", "customer")):
        return "customer"
    if _contains_any(q, ("卖家", "seller")):
        return "seller"
    if _contains_any(q, ("支付", "payment")):
        return "payment"
    if _contains_any(q, ("评价", "review", "评分")):
        return "review"
    if _contains_any(q, ("商品", "产品", "product", "品类", "类别")):
        return "product"
    return "order"


def _detect_analysis_type(question: str) -> str:
    q = question.casefold()
    has_time_series = _contains_any(
        q, ("趋势", "走势", "月度", "每月", "按月", "季度", "每季度", "逐月", "各月", "月份", "每个月", "全年")
    )
    # Explicit change-rate vs a baseline.
    if _contains_any(q, ("增长率", "变化率", "涨幅", "降幅", "增长了多少", "下降了多少", "涨了多少", "跌了多少", "同比", "环比", "比上月", "比去年", "较上", "较去年")):
        return "change"
    # Trend: time-series change, or explicit trend/causality wording.
    if _contains_any(q, ("趋势", "走势", "为什么", "原因", "变化情况", "怎么变")) or (
        has_time_series and _contains_any(q, ("变化", "下降", "上升", "怎么样", "如何", "怎么"))
    ):
        return "trend"
    if _contains_any(q, ("最多", "最高", "最低", "排名", "前", "top", "最好", "最佳", "最少", "最大")):
        return "ranking"
    if _contains_any(q, ("对比", "比较", "相差", "差异", "哪个多", "哪个高", "高多少", "vs", "哪个", "分别")):
        return "comparison"
    if _contains_any(q, ("占比", "构成", "分布", "比例", "各占")):
        return "composition"
    return "summary"


def _extract_group_by(question: str) -> list[str]:
    q = question.casefold()
    group_by: list[str] = []
    if _contains_any(q, ("按月", "每月", "每个月", "各月", "逐月", "月度")):
        group_by.append("month")
    if _contains_any(q, ("按季度", "每季度", "各季度")):
        group_by.append("quarter")
    if _contains_any(q, ("按年", "每年", "各年")):
        group_by.append("year")
    if _contains_any(q, ("卖家州", "卖家所在州")):
        group_by.append("seller_state")
    elif _contains_any(q, ("按州", "每个州", "各州", "客户州", "哪个州", "哪些州", "州分布")):
        group_by.append("customer_state")
    if _contains_any(q, ("类别", "品类", "各品类", "按类别")):
        group_by.append("category_name")
    if _contains_any(q, ("支付方式", "各支付", "按支付")):
        group_by.append("payment_type")
    if _contains_any(q, ("状态分布", "各种订单状态", "各状态", "按状态", "状态占比", "各订单状态")):
        group_by.append("status")
    return group_by


def _extract_rank(question: str) -> tuple[int | None, str | None]:
    q = question.casefold()
    limit = None
    patterns = (
        r"(?:前|top\s*|top-?)\s*([0-9]+|[一二两三四五六七八九十]{1,3})",
        r"(?:哪)\s*([0-9]+|[一二两三四五六七八九十]{1,3})\s*[个名位]?",
        r"(?:最大|最高|最多|最好|最低|最少|最小)的?\s*([0-9]+|[一二两三四五六七八九十]{1,3})\s*[个名位]?",
        r"(?<![\d])([0-9]+)\s*[个名位]",
    )
    for pattern in patterns:
        match = re.search(pattern, q)
        if match:
            limit = _cn_numeral_to_int(match.group(1))
            if limit:
                break
    order = None
    if _contains_any(q, ("最多", "最高", "最好", "最佳", "最大", "排名", "top", "前")):
        order = "desc"
    elif _contains_any(q, ("最少", "最低", "最小")):
        order = "asc"
    return limit, order


def _extract_states(text: str) -> list[str]:
    states: list[str] = []
    for name, code in _STATE_ALIASES.items():
        if name in text:
            states.append(code)
    for match in _STATE_CODE.finditer(text):
        code = match.group(1).upper()
        if code in _STATE_CODES:
            states.append(code)
    return list(dict.fromkeys(states))


def _extract_branch_filters(branch: str) -> list[FilterNode]:
    conditions: list[FilterNode] = []
    states = _extract_states(branch)
    if states:
        conditions.append(FilterNode(op="in", field="customer_state", value=states))

    neg = _STATUS_NEGATION.search(branch)
    neg_value = _map_status(neg.group(1)) if neg else None
    if neg_value:
        conditions.append(FilterNode(op="ne", field="status", value=neg_value))
    elif _contains_any(branch, ("已签收", "已交付", "已送达", "delivered")):
        conditions.append(FilterNode(op="eq", field="status", value="delivered"))

    if _contains_any(branch, _UNDELIVERED_TERMS):
        conditions.append(FilterNode(op="undelivered", field="status", value="governed"))

    if _contains_any(branch, ("取消", "canceled", "cancelled")) and neg_value != "canceled":
        if _contains_any(branch, ("除", "不含", "排除", "之外", "以外", "外")):
            conditions.append(FilterNode(op="ne", field="status", value="canceled"))
        else:
            conditions.append(FilterNode(op="eq", field="status", value="canceled"))

    gt = _VALUE_GT.search(branch)
    if gt:
        conditions.append(FilterNode(op="gt", field=_value_filter_field(gt.group(1)), value=float(gt.group(2))))
    lt = _VALUE_LT.search(branch)
    if lt:
        conditions.append(FilterNode(op="lt", field=_value_filter_field(lt.group(1)), value=float(lt.group(2))))
    return conditions


def _has_multi_state_filter(node: FilterNode | None) -> bool:
    if node is None:
        return False
    if node.op == "in" and node.field == "customer_state" and isinstance(node.value, list) and len(node.value) > 1:
        return True
    return any(_has_multi_state_filter(child) for child in node.children)


def _extract_filters(question: str) -> FilterNode | None:
    branches = _OR_SPLIT.split(question)
    branch_nodes: list[FilterNode] = []
    for branch in branches:
        conditions = _extract_branch_filters(branch)
        if not conditions:
            continue
        if len(conditions) == 1:
            branch_nodes.append(conditions[0])
        else:
            branch_nodes.append(FilterNode(op="and", children=conditions))
    if not branch_nodes:
        return None
    if len(branch_nodes) == 1:
        return branch_nodes[0]
    return FilterNode(op="or", children=branch_nodes)


def _collect_ambiguities(
    question: str,
    metric: str | None,
    counting_unit: str | None,
    analysis_type: str,
    group_by: list[str],
    limit: int | None,
    sort_order: str | None,
) -> list[str]:
    q = question.casefold()
    ambiguities: list[str] = []

    if metric is None and counting_unit is None:
        if _contains_any(q, ("情况", "状况", "表现")):
            ambiguities.append("metric_unknown")

    if analysis_type == "change" and not _contains_any(
        q, ("环比", "同比", "相对", "上月", "去年", "mom", "yoy", "vs", "相比", "较上", "较去年")
    ):
        ambiguities.append("comparison_baseline_missing")
    if analysis_type == "comparison" and not group_by and not _contains_any(q, ("vs", "对比", "比较", "相比", "哪个", "哪些", "相差", "高多少")):
        ambiguities.append("comparison_target_missing")

    if _contains_any(q, ("地区", "区域")) and not _contains_any(q, ("客户", "卖家", "消费者", "customer", "seller")):
        ambiguities.append("dimension_ambiguous")

    return list(dict.fromkeys(ambiguities))


_COMPLETE_PLAN_METRICS = {
    "undelivered",
    "orders_by_status",
    "repeat_customers",
    "delivered_customer_gmv_percentile",
    "average_order_value",
    "category_average_order_value",
    "monthly_delivered_gmv",
    "top_category_gmv",
    "top_seller_state_gmv",
    "payment_value_by_type",
}


def _semantic_coverage(metric: str | None, counting_unit: str | None) -> str:
    """Classify how much of the question the deterministic layer fully governs.

    HIGH: a metric with a complete deterministic plan, or a clear order-count
          grain -> deterministic plan is authoritative.
    PARTIAL: a recognized metric/grain without a complete plan -> plan is only
          a weak hint; rely on metric guidance + legacy pipeline.
    LOW: nothing reliably parsed -> fall back to the legacy pipeline.
    """
    if metric in _COMPLETE_PLAN_METRICS or counting_unit == "order":
        return "high"
    if metric is not None or counting_unit in {"value", "item", "distinct_product"}:
        return "partial"
    return "low"


def build_structured_intent(question: str) -> StructuredIntent:
    metric = identify_metric(question)
    counting_unit = _extract_counting_unit(question, metric)
    aggregation = _infer_aggregation(metric, counting_unit)
    entity = _extract_entity(question, metric)
    analysis_type = _detect_analysis_type(question)
    time_range = _extract_time_range(question)
    group_by = _extract_group_by(question)
    limit, sort_order = _extract_rank(question)
    filters = _extract_filters(question)

    # Infer the ranking dimension when the entity is clearly customer orders.
    q = question.casefold()
    if analysis_type == "ranking" and not group_by:
        if _contains_any(q, ("州",)):
            group_by = ["customer_state"]
        elif _contains_any(q, ("类别", "品类")):
            group_by = ["category_name"]
        elif _contains_any(q, ("支付方式",)):
            group_by = ["payment_type"]

    if analysis_type == "comparison" and not group_by and _has_multi_state_filter(filters):
        group_by = ["customer_state"]

    ambiguities = _collect_ambiguities(
        question, metric, counting_unit, analysis_type, group_by, limit, sort_order
    )

    business_concepts = [metric] if metric else []
    coverage = _semantic_coverage(metric, counting_unit)
    if coverage == "high" and not ambiguities:
        confidence = "high"
    elif coverage != "low" and not ambiguities:
        confidence = "medium"
    else:
        confidence = "low"

    _time_dims = {"month", "quarter", "year"}
    has_time_group = any(dim in _time_dims for dim in group_by)
    if sort_order is None:
        if has_time_group:
            sort_order = "asc"
        elif group_by and counting_unit in {"order", "item", "value", "distinct_product"}:
            sort_order = "desc"

    sort_field = None
    if sort_order:
        if analysis_type == "ranking":
            sort_field = "metric"
        elif has_time_group:
            sort_field = next(dim for dim in group_by if dim in _time_dims)
        elif group_by and counting_unit in {"order", "item", "value", "distinct_product"}:
            sort_field = "metric"
        elif group_by:
            sort_field = group_by[0]
        elif metric:
            sort_field = metric

    return StructuredIntent(
        entity=entity,
        metric=metric,
        aggregation=aggregation,
        counting_unit=counting_unit,
        time_range=time_range,
        dimensions=group_by,
        filters=filters,
        group_by=group_by,
        sort_field=sort_field,
        sort_order=sort_order,
        limit=limit,
        comparison=None,
        analysis_type=analysis_type,
        business_concepts=business_concepts,
        ambiguities=ambiguities,
        confidence=confidence,
        semantic_coverage=coverage,
    )


def clarification_decision(intent: StructuredIntent) -> dict[str, Any]:
    ambiguities = intent.ambiguities
    if not ambiguities:
        return {"needs_clarification": False, "message": "", "options": []}

    if "metric_unknown" in ambiguities and "dimension_ambiguous" in ambiguities:
        return {
            "needs_clarification": True,
            "message": (
                "“表现最好”缺少明确的统计指标，且“地区”未指明是客户州还是卖家州。"
                "请补充指标与维度。"
            ),
            "options": [
                {"label": "客户州 × 销售额", "question": "按客户州统计已签收销售额，最高的是哪个州？"},
                {"label": "卖家州 × 销售额", "question": "按卖家州统计已签收销售额，最高的是哪个州？"},
                {"label": "客户州 × 订单量", "question": "按客户州统计订单量，最高的是哪个州？"},
            ],
        }

    if "metric_unknown" in ambiguities:
        return {
            "needs_clarification": True,
            "message": (
                "当前问题的统计口径不明确。请明确要统计：订单数、商品件数、"
                "商品金额，还是按品类/状态分布。"
            ),
            "options": [
                {"label": "按订单数", "question": "订单数量是多少？"},
                {"label": "按商品件数", "question": "商品件数是多少？"},
                {"label": "按商品金额", "question": "商品金额是多少？"},
            ],
        }

    if "comparison_baseline_missing" in ambiguities:
        return {
            "needs_clarification": True,
            "message": "请明确增长的对比基准：环比上月，还是同比去年同期？",
            "options": [
                {"label": "环比上月", "question": "环比上月的销售额变化是多少？"},
                {"label": "同比去年", "question": "同比去年同期的销售额变化是多少？"},
            ],
        }

    if "comparison_target_missing" in ambiguities:
        return {
            "needs_clarification": True,
            "message": "请明确要与什么进行比较（例如两个州、两个时段或两种支付方式）。",
            "options": [],
        }

    if "dimension_ambiguous" in ambiguities:
        return {
            "needs_clarification": True,
            "message": "“地区”未指明是客户所在州还是卖家所在州，请补充维度。",
            "options": [
                {"label": "客户州", "question": "按客户州统计"},
                {"label": "卖家州", "question": "按卖家州统计"},
            ],
        }

    return {
        "needs_clarification": True,
        "message": "问题存在多种合理解释，请补充关键口径后再查询。",
        "options": [],
    }
