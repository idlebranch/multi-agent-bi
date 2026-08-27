# Multi-Agent BI Benchmark Baseline

- Timestamp (UTC): 2026-08-27T15:41:33.520567+00:00
- Commit: `e1803a056dfb90846793f001584c247c5da0e1c8`
- Benchmark version: `1.0.0`
- Model: `deepseek-chat`
- Business cases: 90
- Safety cases: 25
- Database unchanged: **True**

## Core metrics

| Metric | Result |
|---|---:|
| Overall Execution Accuracy | 91.76% (78/85) |
| Overall Answer Accuracy | 91.11% (82/90) |
| End-to-End Accuracy | 90.00% (81/90) |
| Safety Blocking Rate | 100.00% (25/25) |

## Execution Accuracy by difficulty

| Difficulty | EX | E2E |
|---|---:|---:|
| Easy | 92.31% (24/26) | 92.59% (25/27) |
| Medium | 94.29% (33/35) | 89.47% (34/38) |
| Hard | 87.50% (21/24) | 88.00% (22/25) |

## Results by category

| Category | EX | E2E |
|---|---:|---:|
| ambiguity | unavailable | 100.00% (3/3) |
| complex_filter | 57.14% (4/7) | 57.14% (4/7) |
| empty_result | 100.00% (3/3) | 100.00% (3/3) |
| filtering_sorting | 80.00% (8/10) | 80.00% (8/10) |
| governed_metric | 100.00% (12/12) | 100.00% (12/12) |
| multi_table_join | 87.50% (14/16) | 87.50% (14/16) |
| out_of_domain | unavailable | 100.00% (2/2) |
| ratio_metric | 100.00% (8/8) | 87.50% (7/8) |
| single_table_aggregation | 100.00% (11/11) | 100.00% (11/11) |
| time_series | 100.00% (10/10) | 100.00% (10/10) |
| time_window | 100.00% (8/8) | 87.50% (7/8) |

## Reliability

- Average latency: 3.907 s
- P50 latency: 4.009 s
- P95 latency: 9.601 s
- Maximum latency: 12.556 s
- Average repair count: 0.2111
- Reviewer rejection rate: 20.19% (21/104)
- Actual workflow LLM-stage invoke calls: 325
- Average LLM-stage calls per all request: 2.8261
- Average LLM-stage calls per business case: 3.6111
- Average LLM-stage calls per query case: 3.8118
- LLM-stage breakdown: format_answer=80, schema_linking=37, sql_generation=104, sql_review=104
- Planner/router LLM calls: 0 (routing is deterministic and policy-coded)
- SQL-repair LLM calls: 19
- Exact provider HTTP request count: unavailable; SDK retries are not exposed as HTTP counts
- Token usage: provider-reported prompt=411938, completion=27136, total=439074 (available)
- Average total tokens per business case: 4878.6
- Average total tokens per query case: 5144.576

## Schema context measurements

Counts and character sizes below are measured from the actual workflow state; token counts are not estimated.

| Measurement | All business avg | Query-case avg | Min | Max | Cases |
|---|---:|---:|---:|---:|---:|
| available_table_count | 15.289 | 16 | 0 | 16 | 90 |
| available_column_count | 99.378 | 104 | 0 | 104 | 90 |
| selected_table_count | 1.067 | 1.129 | 0 | 4 | 90 |
| selected_column_count | 5.478 | 5.8 | 0 | 17 | 90 |
| catalog_context_chars | 2032.944 | 2094.353 | 0 | 4945 | 90 |
| selected_schema_context_chars | 2079.067 | 2201.365 | 0 | 4520 | 90 |

## Failure taxonomy

| Failure category | Count | Share of failed cases | Examples |
|---|---:|---:|---|
| filter | 2 | 22.22% | B015, B018 |
| wrong_table | 2 | 22.22% | B029, B078 |
| answer_synthesis | 2 | 22.22% | B050, B068 |
| missing_join | 1 | 11.11% | B034 |
| wrong_join | 1 | 11.11% | B079 |
| metric_definition | 1 | 11.11% | B081 |

## Database protection

- Backend: `postgresql`
- Database: `127.0.0.1:5432/multi_agent_bi`
- Read-only before/after: `True` / `True`
- Dataset fingerprint unchanged: `True`
- Safety execution is intercepted and counted; any call makes the case fail.
