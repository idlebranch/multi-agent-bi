# Historical Baseline → Final PostgreSQL Benchmark

This is a contextual portfolio comparison, not a strict A/B experiment. The database backend, schema/runtime implementation, source commit, evaluator behavior, and request instrumentation differ. Both runs used `deepseek-chat`, but provider behavior may also vary over time.

| Metric | Historical SQLite | Final PostgreSQL | Delta |
|---|---:|---:|---:|
| Execution Accuracy | 90.59% (77/85) | 89.41% (76/85) | -1.18 pp |
| Answer Accuracy | 88.89% (80/90) | 90.00% (81/90) | +1.11 pp |
| End-to-End Accuracy | 88.89% (80/90) | 87.78% (79/90) | -1.11 pp |
| Safety Blocking Rate | 80.00% (20/25) | 100.00% (25/25) | +20.00 pp |
| P50 latency | 4.483 s | 3.925 s | -0.558 s |
| P95 latency | 10.287 s | 10.559 s | +0.272 s |
| Observed LLM stage calls | 405 | 344 | -61 |

The latency reference values `4.483 s / 10.287 s` are the frozen historical values specified for this sprint. The saved audited historical JSON currently summarizes the same stored case latencies as `4.104 s / 9.464 s`; both references are retained in the companion JSON to avoid silently rewriting historical context.

Historical provider token usage was unavailable. The final run captured provider-reported usage for all 344 observed stage calls: 350,464 prompt tokens, 30,757 completion tokens, and 381,221 total tokens. Stage calls are workflow invocations, not exact provider HTTP request counts.

Sources:

- Historical: `benchmark_baseline_20260824_audited.json`, source commit `13fe742`
- Final: `final_benchmark_20260824.json`, evaluated source commit `b4f2a6f`
