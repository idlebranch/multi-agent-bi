"""Progressive SQLite and live-agent load tests with latency percentiles."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import platform
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.batch_test_live import run_case  # noqa: E402
from src.config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL  # noqa: E402
from src.guardrails import sanitize_public_value  # noqa: E402
from src.tools.db_tools import execute_sql, get_db_path  # noqa: E402


DEFAULT_CASES = PROJECT_ROOT / "data" / "olist_advanced_queries.json"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "performance_latest.json"


def parse_levels(raw: str) -> list[int]:
    levels = sorted({int(value.strip()) for value in raw.split(",") if value.strip()})
    if not levels or any(value < 1 or value > 256 for value in levels):
        raise argparse.ArgumentTypeError("concurrency levels must be between 1 and 256")
    return levels


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def process_rss_bytes() -> int | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_bool
    handle = kernel32.GetCurrentProcess()
    success = psapi.GetProcessMemoryInfo(
        handle,
        ctypes.byref(counters),
        counters.cb,
    )
    return int(counters.working_set_size) if success else None


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("cases file must contain a non-empty JSON array")
    return payload


def summarize_results(
    results: list[dict[str, Any]],
    *,
    wall_seconds: float,
    success_key: str,
) -> dict[str, Any]:
    latencies = [float(item["elapsed_seconds"]) for item in results]
    succeeded = sum(bool(item.get(success_key, False)) for item in results)
    return {
        "requests": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "success_rate": round(succeeded / len(results), 4) if results else 0.0,
        "wall_seconds": round(wall_seconds, 3),
        "throughput_per_second": round(len(results) / wall_seconds, 3)
        if wall_seconds
        else 0.0,
        "successful_throughput_per_second": round(succeeded / wall_seconds, 3)
        if wall_seconds
        else 0.0,
        "latency_seconds": {
            "min": round(min(latencies), 4) if latencies else 0.0,
            "p50": round(percentile(latencies, 0.50), 4),
            "p95": round(percentile(latencies, 0.95), 4),
            "p99": round(percentile(latencies, 0.99), 4),
            "max": round(max(latencies), 4) if latencies else 0.0,
        },
    }


def run_parallel_level(
    cases: list[dict[str, Any]],
    *,
    concurrency: int,
    request_count: int,
    runner: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    workload = [cases[index % len(cases)] for index in range(request_count)]
    random.Random(20260717 + concurrency).shuffle(workload)
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(runner, case) for case in workload]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    {
                        "name": "uncaught_exception",
                        "elapsed_seconds": 0.0,
                        "success": False,
                        "stage_success": False,
                        "passed": False,
                        "error": str(exc),
                    }
                )
    return results, time.perf_counter() - started


def benchmark_database(
    cases: list[dict[str, Any]],
    *,
    levels: list[int],
    base_requests: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    database = get_db_path()

    def execute_case(case: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        result = execute_sql(
            str(case["sql"]),
            database,
            max_rows=500,
            timeout_seconds=timeout_seconds,
        )
        return {
            "name": case["name"],
            "elapsed_seconds": time.perf_counter() - started,
            "success": bool(result["success"]),
            "row_count": result["row_count"],
            "error": result["error"],
        }

    warmup = [execute_case(case) for case in cases]
    levels_report: list[dict[str, Any]] = []
    for concurrency in levels:
        request_count = max(base_requests, concurrency * 2)
        rss_before = process_rss_bytes()
        results, wall_seconds = run_parallel_level(
            cases,
            concurrency=concurrency,
            request_count=request_count,
            runner=execute_case,
        )
        rss_after = process_rss_bytes()
        summary = summarize_results(results, wall_seconds=wall_seconds, success_key="success")
        summary.update(
            {
                "concurrency": concurrency,
                "rss_before_bytes": rss_before,
                "rss_after_bytes": rss_after,
                "rss_delta_bytes": (
                    rss_after - rss_before
                    if rss_before is not None and rss_after is not None
                    else None
                ),
                "errors": [
                    {"name": item["name"], "error": item.get("error")}
                    for item in results
                    if not item.get("success")
                ][:20],
                "successes_by_query": dict(
                    Counter(
                        str(item["name"]) for item in results if item.get("success")
                    )
                ),
                "failures_by_query": dict(
                    Counter(
                        str(item["name"]) for item in results if not item.get("success")
                    )
                ),
            }
        )
        levels_report.append(summary)
        print(
            "DB "
            f"c={concurrency}: success={summary['succeeded']}/{summary['requests']} "
            f"qps={summary['throughput_per_second']} "
            f"p95={summary['latency_seconds']['p95']}s",
            flush=True,
        )
    return {
        "database": database.name,
        "database_bytes": database.stat().st_size,
        "query_timeout_seconds": timeout_seconds,
        "warmup_success": sum(bool(item["success"]) for item in warmup),
        "warmup_total": len(warmup),
        "warmup_results": warmup,
        "levels": levels_report,
    }


def benchmark_live_agent(
    cases: list[dict[str, Any]],
    *,
    levels: list[int],
    base_requests: int,
    max_iterations: int,
) -> dict[str, Any]:
    if not DEEPSEEK_API_KEY:
        return {"skipped": True, "reason": "DEEPSEEK_API_KEY is not configured"}

    def execute_case(case: dict[str, Any]) -> dict[str, Any]:
        return run_case(case, version="v1", max_iterations=max_iterations)

    levels_report: list[dict[str, Any]] = []
    for concurrency in levels:
        request_count = max(base_requests, concurrency)
        rss_before = process_rss_bytes()
        results, wall_seconds = run_parallel_level(
            cases,
            concurrency=concurrency,
            request_count=request_count,
            runner=execute_case,
        )
        rss_after = process_rss_bytes()
        summary = summarize_results(
            results,
            wall_seconds=wall_seconds,
            success_key="stage_success",
        )
        semantic_passes = sum(bool(item.get("passed", False)) for item in results)
        summary.update(
            {
                "concurrency": concurrency,
                "semantic_passed": semantic_passes,
                "semantic_pass_rate": round(semantic_passes / len(results), 4),
                "rss_before_bytes": rss_before,
                "rss_after_bytes": rss_after,
                "rss_delta_bytes": (
                    rss_after - rss_before
                    if rss_before is not None and rss_after is not None
                    else None
                ),
                "results": sanitize_public_value(results, max_chars=200_000),
            }
        )
        levels_report.append(summary)
        print(
            "LIVE "
            f"c={concurrency}: workflow={summary['succeeded']}/{summary['requests']} "
            f"semantic={semantic_passes}/{summary['requests']} "
            f"rps={summary['throughput_per_second']} "
            f"p95={summary['latency_seconds']['p95']}s",
            flush=True,
        )
    return {
        "model": DEEPSEEK_MODEL,
        "max_iterations": max_iterations,
        "levels": levels_report,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("db", "live", "all"), default="all")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--names", nargs="*", default=[], help="run only named cases")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--db-levels", type=parse_levels, default=parse_levels("1,4,8,16,32,64"))
    parser.add_argument("--db-base-requests", type=int, default=24)
    parser.add_argument("--db-timeout", type=float, default=5.0)
    parser.add_argument("--live-levels", type=parse_levels, default=parse_levels("1,2,4,8"))
    parser.add_argument("--live-base-requests", type=int, default=8)
    parser.add_argument("--max-iterations", type=int, default=12)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_cases(args.cases.resolve())
    if args.names:
        selected_names = set(args.names)
        cases = [case for case in cases if case["name"] in selected_names]
        if not cases:
            raise ValueError("none of the requested case names exist")
    started = time.perf_counter()
    report: dict[str, Any] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "cases_file": args.cases.name,
        "case_count": len(cases),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_processors": os.cpu_count(),
            "process_rss_bytes_at_start": process_rss_bytes(),
        },
    }
    if args.mode in {"db", "all"}:
        report["database_benchmark"] = benchmark_database(
            cases,
            levels=args.db_levels,
            base_requests=args.db_base_requests,
            timeout_seconds=args.db_timeout,
        )
    if args.mode in {"live", "all"}:
        report["live_agent_benchmark"] = benchmark_live_agent(
            cases,
            levels=args.live_levels,
            base_requests=args.live_base_requests,
            max_iterations=args.max_iterations,
        )
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    report["environment"]["process_rss_bytes_at_end"] = process_rss_bytes()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Performance report: {args.report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
