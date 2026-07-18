"""Deterministic input, context, result, and public-output guardrails."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Sequence


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
            r"(reveal|show|print|return).{0,30}(api[_\s-]?key|password|secret|connection string)|"
            r"(输出|显示|泄露).{0,20}(密钥|密码|连接字符串)",
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
    return {
        "question": normalized,
        "status": "blocked" if flags else "passed",
        "risk_flags": flags,
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
