"""Run repeatable PostgreSQL bounded-concurrency reliability scenarios."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    {
        "name": "concurrency_1",
        "limit": 1,
        "requests": 6,
        "workers": 6,
        "hold_seconds": 0.05,
        "capacity_wait_timeout_seconds": 2.0,
        "expect_capacity_timeout": False,
    },
    {
        "name": "configured_limit",
        "limit": 4,
        "requests": 12,
        "workers": 12,
        "hold_seconds": 0.05,
        "capacity_wait_timeout_seconds": 2.0,
        "expect_capacity_timeout": False,
    },
    {
        "name": "above_limit",
        "limit": 4,
        "requests": 12,
        "workers": 12,
        "hold_seconds": 0.35,
        "capacity_wait_timeout_seconds": 0.1,
        "expect_capacity_timeout": True,
    },
)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 3)


def _worker(scenario: dict[str, Any]) -> int:
    os.environ["BI_DB_MAX_CONCURRENCY"] = str(scenario["limit"])
    os.environ["BI_DB_QUEUE_TIMEOUT_SECONDS"] = str(
        scenario["capacity_wait_timeout_seconds"]
    )
    os.environ["BI_SQL_TIMEOUT_SECONDS"] = "2"
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from src.tools.db_tools import (  # noqa: PLC0415
        execute_sql,
        get_database_label,
        get_db_capacity_snapshot,
        reset_db_capacity_metrics,
    )

    warmup = execute_sql("SELECT 1 AS ready", timeout_seconds=2)
    if not warmup["success"]:
        print(
            json.dumps(
                {
                    "scenario": scenario,
                    "passed": False,
                    "setup_error": str(warmup.get("error_code") or "database_error"),
                }
            )
        )
        return 1

    reset_db_capacity_metrics()
    request_count = int(scenario["requests"])
    barrier = threading.Barrier(request_count + 1)
    sql = (
        "WITH delay AS MATERIALIZED (SELECT pg_sleep("
        f"{float(scenario['hold_seconds']):.3f}"
        ")) SELECT COUNT(*) AS order_count FROM orders CROSS JOIN delay"
    )

    def run_one(request_number: int) -> dict[str, Any]:
        barrier.wait()
        started = time.perf_counter()
        result = execute_sql(sql, timeout_seconds=2)
        return {
            "request_number": request_number,
            "success": bool(result["success"]),
            "error_code": result.get("error_code"),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "capacity_wait_ms": float(result.get("capacity_wait_ms", 0.0)),
        }

    with ThreadPoolExecutor(max_workers=int(scenario["workers"])) as executor:
        futures = [executor.submit(run_one, index) for index in range(request_count)]
        started = time.perf_counter()
        barrier.wait()
        results = [future.result() for future in futures]
    elapsed = time.perf_counter() - started

    latencies = [float(item["latency_ms"]) for item in results]
    successes = sum(item["success"] for item in results)
    capacity_timeouts = sum(item.get("error_code") == "queue_timeout" for item in results)
    other_failures = request_count - successes - capacity_timeouts
    capacity = get_db_capacity_snapshot()
    expects_timeout = bool(scenario["expect_capacity_timeout"])
    passed = bool(
        int(capacity["max_active"]) <= int(scenario["limit"])
        and other_failures == 0
        and ((successes == request_count and capacity_timeouts == 0) if not expects_timeout else capacity_timeouts > 0)
    )
    payload = {
        "scenario": scenario,
        "passed": passed,
        "database_label": get_database_label(),
        "mechanism": capacity["mechanism"],
        "requests": request_count,
        "successes": successes,
        "capacity_timeouts": capacity_timeouts,
        "other_failures": other_failures,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_requests_per_second": round(request_count / elapsed, 3),
        "latency_ms": {
            "average": round(statistics.mean(latencies), 3),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "maximum": round(max(latencies), 3),
        },
        "capacity": capacity,
        "request_results": results,
    }
    print(json.dumps(payload, separators=(",", ":")))
    return 0 if passed else 1


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Final Reliability Report",
        "",
        f"- Timestamp (UTC): {report['metadata']['timestamp_utc']}",
        f"- Source commit: `{report['metadata'].get('commit_sha', 'unavailable')}`",
        f"- Database: `{report['metadata']['database_label']}`",
        "- Mechanism: `threading.BoundedSemaphore` enforcing bounded database concurrency with a timed capacity wait.",
        "- There is no independent message queue, queue broker, or persistent queue length.",
        "- Workload: read-only PostgreSQL queries with a controlled `pg_sleep`; no LLM calls.",
        "",
        "| Scenario | Limit | Requests | Success | Capacity timeout | Other failure | Throughput req/s | P50 ms | P95 ms | Max ms | Max active | Max waiting | Pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["scenarios"]:
        scenario = item["scenario"]
        latency = item["latency_ms"]
        capacity = item["capacity"]
        lines.append(
            f"| {scenario['name']} | {scenario['limit']} | {item['requests']} | "
            f"{item['successes']} | {item['capacity_timeouts']} | {item['other_failures']} | "
            f"{item['throughput_requests_per_second']} | {latency['p50']} | "
            f"{latency['p95']} | {latency['maximum']} | {capacity['max_active']} | "
            f"{capacity['max_waiting']} | {'PASS' if item['passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Capacity behavior",
            "",
            "At or below the configured limit, requests must complete without capacity timeouts. "
            "Above the limit, excess callers wait only for the configured bounded interval; callers "
            "that cannot acquire capacity return the compatibility error code `queue_timeout`, whose "
            "message now accurately describes a capacity-wait timeout.",
            "",
            f"Overall result: **{'PASS' if report['passed'] else 'FAIL'}**",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-scenario")
    args = parser.parse_args()
    if args.worker_scenario:
        return _worker(json.loads(args.worker_scenario))
    if args.output is None:
        parser.error("--output is required")

    results: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        env = os.environ.copy()
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker-scenario",
                json.dumps(scenario, separators=(",", ":")),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if not output_lines:
            raise RuntimeError(
                f"reliability worker {scenario['name']} produced no JSON output"
            )
        result = json.loads(output_lines[-1])
        if completed.returncode != 0:
            result["passed"] = False
        results.append(result)

    report = {
        "metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "commit_sha": _git_sha(),
            "database_label": results[0].get("database_label", "unavailable"),
            "workload": "deterministic_read_only_postgresql",
            "llm_invoked": False,
        },
        "passed": all(item.get("passed") for item in results),
        "scenarios": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    print(f"report={args.output.resolve()}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
