# Multi-Agent BI Benchmark Baseline

- Timestamp (UTC): 2026-08-24T14:42:04.812591+00:00
- Commit: `534f427ce0f6ebcf79ef4bf83e9132c9b5bf4da7`
- Benchmark version: `1.0.0`
- Model: `deepseek-chat`
- Business cases: 3
- Safety cases: 2
- Database unchanged: **True**

## Core metrics

| Metric | Result |
|---|---:|
| Overall Execution Accuracy | 66.67% (2/3) |
| Overall Answer Accuracy | 33.33% (1/3) |
| End-to-End Accuracy | 33.33% (1/3) |
| Safety Blocking Rate | 100.00% (2/2) |

## Execution Accuracy by difficulty

| Difficulty | EX | E2E |
|---|---:|---:|
| Easy | unavailable | unavailable |
| Medium | 100.00% (1/1) | 100.00% (1/1) |
| Hard | 50.00% (1/2) | 0.00% (0/2) |

## Results by category

| Category | EX | E2E |
|---|---:|---:|
| multi_table_join | 0.00% (0/1) | 0.00% (0/1) |
| time_series | 100.00% (1/1) | 100.00% (1/1) |
| time_window | 100.00% (1/1) | 0.00% (0/1) |

## Reliability

- Average latency: 3.91 s
- P50 latency: 3.413 s
- P95 latency: 10.177 s
- Maximum latency: 10.177 s
- Average repair count: 0.3333
- Reviewer rejection rate: 25.00% (1/4)
- Actual workflow LLM-stage invoke calls: 13
- LLM-stage breakdown: format_answer=3, schema_linking=2, sql_generation=4, sql_review=4
- SQL-repair LLM calls: 1
- Exact provider HTTP request count: unavailable; SDK retries are not exposed as HTTP counts
- Token usage: provider-reported prompt=15374, completion=1164, total=16538 (available)

## Schema context measurements

Counts and character sizes below are measured from the actual workflow state; token counts are not estimated.

| Measurement | Average | Min | Max | Cases |
|---|---:|---:|---:|---:|
| available_table_count | 16 | 16 | 16 | 3 |
| available_column_count | 104 | 104 | 104 | 3 |
| selected_table_count | 1.333 | 1 | 2 | 3 |
| selected_column_count | 4.667 | 2 | 8 | 3 |
| catalog_context_chars | 3188 | 0 | 4782 | 3 |
| selected_schema_context_chars | 3096.333 | 2335 | 3477 | 3 |

## Failure taxonomy

| Failure category | Count | Share of failed cases | Examples |
|---|---:|---:|---|
| missing_join | 1 | 50.00% | B034 |
| answer_synthesis | 1 | 50.00% | B053 |

## Database protection

- Backend: `postgresql`
- Database: `127.0.0.1:5432/multi_agent_bi`
- Read-only before/after: `True` / `True`
- Dataset fingerprint unchanged: `True`
- Safety execution is intercepted and counted; any call makes the case fail.
