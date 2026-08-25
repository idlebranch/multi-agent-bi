"""Deterministic input, context, result, and public-output guardrails."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Sequence

from src.semantic_rules import (
    question_time_range_entirely_before_start,
    undelivered_metric_is_ambiguous,
)


_RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"ignore\s+(all\s+)?(previous|prior)\s+instructions|"
            r"忽略.{0,20}(之前|以上|前面).{0,20}(指令|规则|提示)|"
            r"developer\s+mode|开发者模式|绕过.{0,20}(规则|限制)",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_extraction",
        re.compile(
            r"(reveal|show|print|repeat).{0,30}system\s+prompt|"
            r"(输出|显示|泄露|重复).{0,20}(系统提示词|内部指令)",
            re.IGNORECASE,
        ),
    ),
    (
        "secret_extraction",
        re.compile(
            r"(reveal|show|print|return|export|输出|显示|泄露|读取|导出)"
            r".{0,50}(api[_\s-]?key|github\s*token|token|password|secret|"
            r"connection\s*string|environment\s*variable|密钥|密码|令牌|连接字符串|环境变量)",
            re.IGNORECASE,
        ),
    ),
    (
        "database_write",
        re.compile(
            r"\b(?:insert|update|delete|drop|alter|create|attach|detach|pragma|"
            r"replace|truncate|vacuum|reindex|grant|revoke|copy|call|execute|set)\b|"
            r"(?<!已)(?:删除|删掉|清空|更新|插入|新增|创建|建表|删表|修改|授予|撤销).{0,24}"
            r"(?:数据库|数据表|表结构|记录|权限|订单|客户|商品|产品|支付|评价|评论|卖家|(?:[\w.]+\s*)?表)|"
            r"(?:数据库|数据表|表结构|记录|权限|(?:[\w.]+\s*)?表).{0,24}"
            r"(?<!已)(?:删除|删掉|清空|更新|插入|新增|修改|授予|撤销)",
            re.IGNORECASE,
        ),
    ),
    (
        "obfuscated_database_write",
        re.compile(
            r"\b(?:d\s*e\s*l\s*e\s*t\s*e)\s+(?:f\s*r\s*o\s*m)\b|"
            r"\b(?:d\s*r\s*o\s*p|t\s*r\s*u\s*n\s*c\s*a\s*t\s*e)\s+"
            r"(?:t\s*a\s*b\s*l\s*e)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "encoded_execution",
        re.compile(
            r"(?:decode|base64|解码).{0,60}(?:execute|run|执行)|"
            r"(?:execute|run|执行).{0,60}(?:base64|解码)",
            re.IGNORECASE,
        ),
    ),
    (
        "multiple_statements",
        re.compile(
            r";\s*(?:select|with|insert|update|delete|drop|alter|create|attach|"
            r"detach|pragma|replace|truncate)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "execution_fabrication",
        re.compile(
            r"(?:返回|声称|假装|伪造).{0,12}(?:执行)?成功|"
            r"(?:pretend|claim|report).{0,20}(?:executed|success|succeeded)",
            re.IGNORECASE,
        ),
    ),
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def normalize_untrusted_text(value: str, *, max_chars: int = 10_000) -> str:
    """Normalize text and remove control/invisible characters with a hard size cap."""
    normalized = unicodedata.normalize("NFKC", value)
    cleaned = "".join(
        char
        for char in normalized
        if char in "\n\r\t" or unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    )
    return cleaned[:max_chars]


def detect_prompt_injection_signals(value: str) -> list[str]:
    normalized = normalize_untrusted_text(value)
    return [name for name, pattern in _RISK_PATTERNS if pattern.search(normalized)]


def screen_user_question(question: str) -> dict[str, Any]:
    normalized = normalize_untrusted_text(question, max_chars=2000).strip()
    flags = detect_prompt_injection_signals(normalized)
    classification = classify_business_question(normalized)
    if flags:
        classification = {
            "request_status": "rejected",
            "request_message": (
                "请求包含数据库写入或规则绕过指令，已被安全策略拒绝。"
                "该系统只允许只读查询。"
            ),
            "clarification_options": [],
        }
    return {
        "question": normalized,
        "status": "rejected" if flags else "passed",
        "risk_flags": flags,
        **classification,
    }


def classify_business_question(question: str) -> dict[str, Any]:
    """Classify deterministic UX cases before schema linking or model calls."""
    value = question.casefold().strip()
    product_terms = ("商品", "产品", "product", "products")
    best_terms = ("最好", "最佳", "最优", "best", "top")
    metric_terms = (
        "销售额",
        "gmv",
        "销量",
        "销售量",
        "评分",
        "评价",
        "订单量",
        "订单数",
        "revenue",
        "sales",
        "rating",
        "orders",
    )
    if (
        any(term in value for term in product_terms)
        and any(term in value for term in best_terms)
        and not any(term in value for term in metric_terms)
    ):
        return {
            "request_status": "clarification_required",
            "request_message": (
                "你希望按照哪个指标判断商品最好：销售额、销量、平均评分，"
                "还是订单量？"
            ),
            "clarification_options": [
                {"label": "按销售额", "question": "销售额最高的五个商品是什么？"},
                {"label": "按销量", "question": "销量最高的五个商品是什么？"},
                {"label": "按评分", "question": "平均评分最高的五个商品是什么？"},
                {"label": "按订单量", "question": "订单量最高的五个商品是什么？"},
            ],
        }

    employee_terms = (
        "员工",
        "雇员",
        "部门",
        "人力资源",
        "绩效",
        "薪资",
        "employee",
        "staff",
        "department",
        "salary",
        "human resources",
    )
    if any(term in value for term in employee_terms):
        return {
            "request_status": "out_of_scope",
            "request_message": (
                "当前 Olist 数据库不包含员工、部门或绩效数据，"
                "因此无法进行员工绩效分析。"
            ),
            "clarification_options": [],
        }

    customer_terms = ("客户", "消费者", "用户", "customer", "consumer")
    demographic_terms = (
        "收入",
        "收入水平",
        "年龄",
        "性别",
        "职业",
        "income",
        "age",
        "gender",
        "occupation",
    )
    if any(term in value for term in customer_terms) and any(
        term in value for term in demographic_terms
    ):
        return {
            "request_status": "out_of_scope",
            "request_message": (
                "当前 Olist 数据库没有客户个人收入/年龄/性别/职业等人口画像字段，"
                "因此无法可靠回答。"
            ),
            "clarification_options": [],
        }

    china_region_terms = (
        "华东",
        "华南",
        "华北",
        "华中",
        "西南地区",
        "西北地区",
        "东北地区",
        "east china",
    )
    if any(term in value for term in china_region_terms):
        return {
            "request_status": "out_of_scope",
            "request_message": (
                "当前 Olist 数据库使用巴西州代码记录客户地区，不包含中国“华东地区”"
                "字段或对应关系，因此无法可靠统计华东地区订单。你可以改为查询某个巴西州，"
                "例如 SP、RJ 或 MG。"
            ),
            "clarification_options": [],
        }

    if question_time_range_entirely_before_start(value):
        return {
            "request_status": "out_of_scope",
            "request_message": (
                "当前数据集从 2016-09-04 开始，请求的时间范围没有数据。"
            ),
            "clarification_options": [],
        }

    if undelivered_metric_is_ambiguous(value):
        return {
            "request_status": "clarification_required",
            "request_message": (
                "“未签收/未送达”是订单级状态，而“商品情况”的统计口径不明确。"
                "请明确要统计：未签收订单数、未签收商品件数，还是未签收商品金额。"
            ),
            "clarification_options": [
                {"label": "按订单数", "question": "未签收订单数量是多少？"},
                {"label": "按商品件数", "question": "未签收商品件数是多少？"},
                {"label": "按商品金额", "question": "未签收商品金额是多少？"},
            ],
        }

    return {
        "request_status": "ready",
        "request_message": "",
        "clarification_options": [],
    }


def redact_secrets(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1) if match.groups() else ''}[REDACTED]", redacted)
    return redacted


def untrusted_text_block(label: str, value: str, *, max_chars: int = 10_000) -> str:
    """Render untrusted text as JSON data inside an explicit non-instruction block."""
    safe_label = re.sub(r"[^A-Z0-9_]", "_", label.upper())[:50]
    normalized = redact_secrets(normalize_untrusted_text(value, max_chars=max_chars))
    return (
        f"<UNTRUSTED_{safe_label}_DATA>\n"
        f"{json.dumps(normalized, ensure_ascii=False)}\n"
        f"</UNTRUSTED_{safe_label}_DATA>"
    )


def _sanitize_result_value(value: Any, *, for_llm: bool, max_chars: int) -> Any:
    if isinstance(value, str):
        normalized = redact_secrets(normalize_untrusted_text(value, max_chars=max_chars))
        flags = detect_prompt_injection_signals(normalized)
        if for_llm and flags:
            return f"[已隔离疑似提示注入文本: {', '.join(flags)}]"
        return normalized
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return normalize_untrusted_text(str(value), max_chars=max_chars)


def sanitize_result_rows(
    rows: Sequence[dict[str, Any]],
    *,
    for_llm: bool,
    max_rows: int = 50,
    max_cell_chars: int = 500,
) -> list[dict[str, Any]]:
    return [
        {
            normalize_untrusted_text(str(key), max_chars=100): _sanitize_result_value(
                value,
                for_llm=for_llm,
                max_chars=max_cell_chars,
            )
            for key, value in row.items()
        }
        for row in rows[:max_rows]
    ]


def sanitize_public_value(value: Any, *, max_chars: int = 2000) -> Any:
    """Recursively remove secrets and controls before values leave the API boundary."""
    if isinstance(value, str):
        return redact_secrets(normalize_untrusted_text(value, max_chars=max_chars))
    if isinstance(value, list):
        return [sanitize_public_value(item, max_chars=max_chars) for item in value]
    if isinstance(value, dict):
        return {
            normalize_untrusted_text(str(key), max_chars=100): sanitize_public_value(
                item, max_chars=max_chars
            )
            for key, item in value.items()
        }
    return value


def sanitize_model_output(value: str, *, max_chars: int = 10_000) -> str:
    return redact_secrets(normalize_untrusted_text(value, max_chars=max_chars)).strip()
