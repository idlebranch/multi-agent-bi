# Final Reliability Report

- Timestamp (UTC): 2026-08-24T14:40:06.485459+00:00
- Source commit: `b4f2a6fe0085c05ef39048d19d9abeedc4845f8b`
- Database: `127.0.0.1:5432/multi_agent_bi`
- Mechanism: `threading.BoundedSemaphore` enforcing bounded database concurrency with a timed capacity wait.
- There is no independent message queue, queue broker, or persistent queue length.
- Workload: read-only PostgreSQL queries with a controlled `pg_sleep`; no LLM calls.

| Scenario | Limit | Requests | Success | Capacity timeout | Other failure | Throughput req/s | P50 ms | P95 ms | Max ms | Max active | Max waiting | Pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| concurrency_1 | 1 | 6 | 6 | 0 | 0 | 14.57 | 195.639 | 410.534 | 410.534 | 1 | 5 | PASS |
| configured_limit | 4 | 12 | 12 | 0 | 0 | 54.631 | 133.845 | 217.148 | 217.148 | 4 | 8 | PASS |
| above_limit | 4 | 12 | 4 | 8 | 0 | 32.416 | 105.456 | 369.506 | 369.506 | 4 | 8 | PASS |

## Capacity behavior

At or below the configured limit, requests must complete without capacity timeouts. Above the limit, excess callers wait only for the configured bounded interval; callers that cannot acquire capacity return the compatibility error code `queue_timeout`, whose message now accurately describes a capacity-wait timeout.

Overall result: **PASS**
