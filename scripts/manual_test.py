"""Friendly interactive and one-shot manual tester for the secured BI workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import (  # noqa: E402
    DATASET_NAME,
    DEEPSEEK_API_KEY,
    DEFAULT_MAX_ITERATIONS,
    get_data_as_of_date,
)
from src.graph import app as production_graph  # noqa: E402
from src.guardrails import sanitize_public_value, sanitize_result_rows  # noqa: E402
from src.policy import policy_limit  # noqa: E402
from src.state import create_initial_state  # noqa: E402
from src.tools.db_tools import get_database_health_summary  # noqa: E402
from src.workflow import run_graph_once  # noqa: E402


EXAMPLE_QUESTIONS = (
    "已签收订单的平均客单价是多少？",
    "按月统计已签收商品 GMV",
    "销售额最高的五个商品类别是什么？",
    "各支付方式的支付金额是多少？",
    "按时送达率是多少？",
    "下过两次及以上订单的消费者有多少？",
)


def _json(value: Any) -> str:
    return json.dumps(
        sanitize_public_value(value),
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def print_environment() -> None:
    try:
        health = get_database_health_summary(force_refresh=True)
        database_text = (
            f"{health['database_label']} "
            f"(PostgreSQL {health['server_version']}, {health['size_mib']} MiB)"
        )
    except RuntimeError as exc:
        database_text = f"不可用：{exc}"

    print("=" * 72)
    print("Multi-Agent BI 人工测试台")
    print("=" * 72)
    print(f"数据集   : {DATASET_NAME}")
    print(f"数据库   : {database_text}")
    print(f"数据日期 : {get_data_as_of_date()}")
    print(f"模型密钥 : {'已配置' if DEEPSEEK_API_KEY else '未配置（普通问题无法调用 Agent）'}")
    print("输入 /help 查看命令，输入 /examples 查看示例，输入 /quit 退出。")


def render_result(final_state: dict[str, Any], trace: list[dict], *, show_trace: bool) -> None:
    print("\n" + "-" * 72)
    print(f"Run ID : {final_state.get('run_id', '')}")
    print(f"输入防护: {final_state.get('input_guard_status', 'unknown')}")
    print(f"响应分类: {final_state.get('response_status', 'unknown')}")
    if final_state.get("input_risk_flags"):
        print(f"风险标记: {', '.join(final_state['input_risk_flags'])}")

    print("\n[最终回答]")
    print(sanitize_public_value(final_state.get("final_answer", "")))

    sql = final_state.get("sql", "")
    if sql:
        print("\n[SQL]")
        print(sanitize_public_value(sql, max_chars=100_000))

    rows = sanitize_result_rows(
        final_state.get("sql_result", []),
        for_llm=False,
        max_rows=20,
    )
    if rows:
        print(f"\n[结果预览：{len(rows)}/{final_state.get('result_row_count', len(rows))} 行]")
        print(_json(rows))

    issues = final_state.get("review_issues", [])
    if issues:
        print("\n[Reviewer 意见]")
        for issue in issues:
            print(
                f"- {issue.get('severity', '?')}/{issue.get('code', 'other')}: "
                f"{sanitize_public_value(issue.get('message', ''))}"
            )

    handoffs = final_state.get("handoff_history", [])
    if handoffs:
        print("\n[Agent 交接]")
        for event in handoffs:
            print(
                f"- {event.get('from_agent')} -> {event.get('to_agent')} "
                f"[{event.get('reason_code')}]"
            )

    denied = [
        decision
        for decision in final_state.get("policy_decisions", [])
        if not decision.get("allowed", False)
    ]
    if denied:
        print("\n[策略拒绝]")
        for decision in denied:
            print(
                f"- {decision.get('action')}: "
                f"{sanitize_public_value(decision.get('reason', ''))}"
            )

    print(
        "\n[状态] "
        f"schema={final_state.get('schema_status')} | "
        f"review={final_state.get('review_status')} | "
        f"validation={final_state.get('validation_status')} | "
        f"execution={final_state.get('execution_status')}"
    )
    print(
        "[性能] "
        f"total={final_state.get('total_duration_ms', 0):.1f} ms | "
        f"repairs={max(0, len(final_state.get('sql_attempt_history', [])) - 1)}"
    )

    if show_trace:
        print("\n[完整 Trace]")
        print(_json(trace))
    print("-" * 72)


def run_question(
    question: str,
    *,
    max_iterations: int,
    show_trace: bool,
) -> dict[str, Any]:
    initial_state = create_initial_state(question, max_iterations=max_iterations)
    final_state, trace = run_graph_once(production_graph, initial_state)
    render_result(final_state, trace, show_trace=show_trace)
    return final_state


def print_help() -> None:
    print(
        """
命令：
  /examples   显示可直接测试的问题
  /trace on   显示每个节点的完整 trace
  /trace off  隐藏完整 trace
  /status     显示当前数据集和模型配置
  /help       显示本帮助
  /quit       退出

直接输入中文问题即可执行。普通问题需要在 .env 中配置 DEEPSEEK_API_KEY。
""".strip()
    )


def interactive_loop(*, max_iterations: int, show_trace: bool) -> int:
    print_environment()
    while True:
        try:
            value = input("\nBI[Production]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return 0

        if not value:
            continue
        command = value.lower()
        if command in {"/quit", "/exit", "quit", "exit"}:
            print("已退出。")
            return 0
        if command == "/help":
            print_help()
            continue
        if command == "/examples":
            for index, example in enumerate(EXAMPLE_QUESTIONS, start=1):
                print(f"{index}. {example}")
            continue
        if command == "/status":
            print_environment()
            continue
        if command in {"/trace on", "/trace off"}:
            show_trace = command.endswith("on")
            print(f"完整 trace：{'开启' if show_trace else '关闭'}")
            continue

        try:
            run_question(
                value,
                max_iterations=max_iterations,
                show_trace=show_trace,
            )
        except Exception as exc:
            print(f"\n测试执行失败：{sanitize_public_value(str(exc))}")


def parse_args() -> argparse.Namespace:
    policy_max = int(policy_limit("workflow_iterations", 12))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="*", help="单次测试问题；省略则进入交互模式")
    parser.add_argument(
        "--max-iterations",
        type=int,
        choices=range(1, policy_max + 1),
        default=min(DEFAULT_MAX_ITERATIONS, policy_max),
    )
    parser.add_argument("--trace", action="store_true", help="显示完整节点 trace")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    question = " ".join(args.question).strip()
    if question:
        run_question(
            question,
            max_iterations=args.max_iterations,
            show_trace=args.trace,
        )
        return 0
    return interactive_loop(
        max_iterations=args.max_iterations,
        show_trace=args.trace,
    )


if __name__ == "__main__":
    raise SystemExit(main())
