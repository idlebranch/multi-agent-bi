# Multi-Agent BI Benchmark Baseline

- Timestamp (UTC): 2026-08-25T07:42:10.659044+00:00
- Commit: `unavailable`
- Benchmark version: `1.0.0`
- Model: `deepseek-chat`
- Business cases: 90
- Safety cases: 25
- Database unchanged: **True**

## Core metrics

| Metric | Result |
|---|---:|
| Overall Execution Accuracy | 90.59% (77/85) |
| Overall Answer Accuracy | 90.00% (81/90) |
| End-to-End Accuracy | 88.89% (80/90) |
| Safety Blocking Rate | 100.00% (25/25) |

## Execution Accuracy by difficulty

| Difficulty | EX | E2E |
|---|---:|---:|
| Easy | 92.31% (24/26) | 92.59% (25/27) |
| Medium | 91.43% (32/35) | 86.84% (33/38) |
| Hard | 87.50% (21/24) | 88.00% (22/25) |

## Results by category

| Category | EX | E2E |
|---|---:|---:|
| ambiguity | unavailable | 66.67% (2/3) |
| complex_filter | 100.00% (7/7) | 100.00% (7/7) |
| empty_result | 100.00% (3/3) | 100.00% (3/3) |
| filtering_sorting | 90.00% (9/10) | 90.00% (9/10) |
| governed_metric | 100.00% (12/12) | 100.00% (12/12) |
| multi_table_join | 81.25% (13/16) | 81.25% (13/16) |
| out_of_domain | unavailable | 100.00% (2/2) |
| ratio_metric | 87.50% (7/8) | 87.50% (7/8) |
| single_table_aggregation | 90.91% (10/11) | 90.91% (10/11) |
| time_series | 90.00% (9/10) | 90.00% (9/10) |
| time_window | 87.50% (7/8) | 75.00% (6/8) |

## Reliability

- Average latency: 3.145 s
- P50 latency: 3.402 s
- P95 latency: 8.555 s
- Maximum latency: 11.693 s
- Average repair count: 0.1667
- Reviewer rejection rate: 17.82% (18/101)
- Actual workflow LLM-stage invoke calls: 333
- Average LLM-stage calls per all request: 2.8957
- Average LLM-stage calls per business case: 3.7
- Average LLM-stage calls per query case: 3.8588
- LLM-stage breakdown: format_answer=79, schema_linking=55, sql_generation=101, sql_review=98
- Planner/router LLM calls: 0 (routing is deterministic and policy-coded)
- SQL-repair LLM calls: 15
- Exact provider HTTP request count: unavailable; SDK retries are not exposed as HTTP counts
- Token usage: provider-reported prompt=346395, completion=26864, total=373259 (available)
- Average total tokens per business case: 4147.322
- Average total tokens per query case: 4313.4

## Schema context measurements

Counts and character sizes below are measured from the actual workflow state; token counts are not estimated.

| Measurement | All business avg | Query-case avg | Min | Max | Cases |
|---|---:|---:|---:|---:|---:|
| available_table_count | 15.467 | 16 | 0 | 16 | 90 |
| available_column_count | 100.533 | 104 | 0 | 104 | 90 |
| selected_table_count | 1.067 | 1.118 | 0 | 3 | 90 |
| selected_column_count | 4.222 | 4.447 | 0 | 17 | 90 |
| catalog_context_chars | 2922.333 | 2981.718 | 0 | 4782 | 90 |
| selected_schema_context_chars | 1816.022 | 1909.176 | 0 | 4140 | 90 |

## Failure taxonomy

| Failure category | Count | Share of failed cases | Examples |
|---|---:|---:|---|
| wrong_table | 2 | 20.00% | B029, B035 |
| metric_definition | 2 | 20.00% | B041, B051 |
| aggregation | 1 | 10.00% | B009 |
| filter | 1 | 10.00% | B018 |
| missing_join | 1 | 10.00% | B034 |
| answer_synthesis | 1 | 10.00% | B050 |
| reviewer_false_positive | 1 | 10.00% | B075 |
| ambiguity_handling | 1 | 10.00% | B085 |

## Database protection

- Backend: `postgresql`
- Database: `postgres:5432/multi_agent_bi`
- Read-only before/after: `True` / `True`
- Dataset fingerprint unchanged: `True`
- Safety execution is intercepted and counted; any call makes the case fail.
