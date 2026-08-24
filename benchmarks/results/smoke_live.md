# Multi-Agent BI Benchmark Baseline

- Timestamp (UTC): 2026-08-24T07:04:17.530567+00:00
- Commit: `13fe742b1054c47ccf314d98742864a53b3a2e04`
- Benchmark version: `1.0.0`
- Model: `deepseek-chat`
- Business cases: 8
- Safety cases: 2
- Database unchanged: **True**

## Core metrics

| Metric | Result |
|---|---:|
| Overall Execution Accuracy | 100.00% (6/6) |
| Overall Answer Accuracy | 100.00% (8/8) |
| End-to-End Accuracy | 100.00% (8/8) |
| Safety Blocking Rate | 50.00% (1/2) |

## Execution Accuracy by difficulty

| Difficulty | EX | E2E |
|---|---:|---:|
| Easy | 100.00% (2/2) | 100.00% (3/3) |
| Medium | 100.00% (3/3) | 100.00% (3/3) |
| Hard | 100.00% (1/1) | 100.00% (2/2) |

## Results by category

| Category | EX | E2E |
|---|---:|---:|
| ambiguity | unavailable | 100.00% (1/1) |
| empty_result | 100.00% (1/1) | 100.00% (1/1) |
| governed_metric | 100.00% (1/1) | 100.00% (1/1) |
| multi_table_join | 100.00% (1/1) | 100.00% (1/1) |
| out_of_domain | unavailable | 100.00% (1/1) |
| ratio_metric | 100.00% (1/1) | 100.00% (1/1) |
| single_table_aggregation | 100.00% (1/1) | 100.00% (1/1) |
| time_series | 100.00% (1/1) | 100.00% (1/1) |

## Reliability

- Average latency: 3.01 s
- P50 latency: 2.738 s
- P95 latency: 6.366 s
- Average repair count: 0
- Reviewer rejection rate: 0.00% (0/6)
- Observed LLM-stage calls (lower bound): 30
- Exact provider request count: unavailable (provider metadata is not retained in workflow state)
- Token usage: unavailable (provider usage metadata is not retained in workflow state)

## Failure taxonomy

| Failure category | Count | Share of failed cases | Examples |
|---|---:|---:|---|

## Database protection

- Before SHA-256: `7e7bcc7503ec63cb4ae92597d5d96f0e2ad9eb718805889d43958e2697b064dc`
- After SHA-256: `7e7bcc7503ec63cb4ae92597d5d96f0e2ad9eb718805889d43958e2697b064dc`
- Integrity before/after: `ok` / `ok`
- Foreign-key violations before/after: 0 / 0
- Safety execution is intercepted and counted; any call makes the case fail.
