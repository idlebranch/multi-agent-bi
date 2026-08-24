# Final PostgreSQL Production Benchmark

- Timestamp (UTC): 2026-08-24T14:52:32.947832+00:00
- Commit: `b4f2a6fe0085c05ef39048d19d9abeedc4845f8b`
- Benchmark version: `1.0.0`
- Model: `deepseek-chat`
- Business cases: 90
- Safety cases: 25
- Database unchanged: **True**
- Source commit context: b4f2a6f; the benchmark ran immediately before commit and the committed source tree matches the evaluated code

## Core metrics

| Metric | Result |
|---|---:|
| Overall Execution Accuracy | 89.41% (76/85) |
| Overall Answer Accuracy | 90.00% (81/90) |
| End-to-End Accuracy | 87.78% (79/90) |
| Safety Blocking Rate | 100.00% (25/25) |

## Execution Accuracy by difficulty

| Difficulty | EX | E2E |
|---|---:|---:|
| Easy | 92.31% (24/26) | 88.89% (24/27) |
| Medium | 91.43% (32/35) | 89.47% (34/38) |
| Hard | 83.33% (20/24) | 84.00% (21/25) |

## Results by category

| Category | EX | E2E |
|---|---:|---:|
| ambiguity | unavailable | 66.67% (2/3) |
| complex_filter | 85.71% (6/7) | 85.71% (6/7) |
| empty_result | 100.00% (3/3) | 100.00% (3/3) |
| filtering_sorting | 90.00% (9/10) | 90.00% (9/10) |
| governed_metric | 100.00% (12/12) | 100.00% (12/12) |
| multi_table_join | 87.50% (14/16) | 87.50% (14/16) |
| out_of_domain | unavailable | 100.00% (2/2) |
| ratio_metric | 87.50% (7/8) | 87.50% (7/8) |
| single_table_aggregation | 90.91% (10/11) | 81.82% (9/11) |
| time_series | 90.00% (9/10) | 90.00% (9/10) |
| time_window | 75.00% (6/8) | 75.00% (6/8) |

## Reliability

- Average latency: 3.765 s
- P50 latency: 3.925 s
- P95 latency: 10.559 s
- Maximum latency: 14.28 s
- Average repair count: 0.2
- Reviewer rejection rate: 22.12% (23/104)
- Actual workflow LLM-stage invoke calls: 344
- Average LLM-stage calls per all request: 2.9913
- Average LLM-stage calls per business case: 3.8222
- LLM-stage breakdown: format_answer=78, schema_linking=57, sql_generation=104, sql_review=105
- Planner/router LLM calls: 0 (routing is deterministic and policy-coded)
- SQL-repair LLM calls: 18
- Exact provider HTTP request count: unavailable; SDK retries are not exposed as HTTP counts
- Token usage: provider-reported prompt=350464, completion=30757, total=381221 (available)
- Average total tokens per business case: 4235.789
- Average total tokens per query case: 4484.953

## Schema context measurements

Counts and character sizes below are measured from the actual workflow state; token counts are not estimated.

| Measurement | All business avg | Query-case avg | Min | Max | Cases |
|---|---:|---:|---:|---:|---:|
| available_table_count | 15.467 | 16 | 0 | 16 | 90 |
| available_column_count | 100.533 | 104 | 0 | 104 | 90 |
| selected_table_count | 1.1 | 1.153 | 0 | 3 | 90 |
| selected_column_count | 4.311 | 4.494 | 0 | 17 | 90 |
| catalog_context_chars | 2975.467 | 3037.976 | 0 | 4782 | 90 |
| selected_schema_context_chars | 1890 | 1987.506 | 0 | 4213 | 90 |

## Failure taxonomy

| Failure category | Count | Share of failed cases | Examples |
|---|---:|---:|---|
| metric_definition | 3 | 27.27% | B050, B051, B081 |
| answer_synthesis | 1 | 9.09% | B001 |
| aggregation | 1 | 9.09% | B009 |
| filter | 1 | 9.09% | B018 |
| wrong_table | 1 | 9.09% | B029 |
| missing_join | 1 | 9.09% | B034 |
| time_logic | 1 | 9.09% | B046 |
| reviewer_false_positive | 1 | 9.09% | B075 |
| ambiguity_handling | 1 | 9.09% | B085 |

## Database protection

- Backend: `postgresql`
- Database: `127.0.0.1:5432/multi_agent_bi`
- Read-only before/after: `True` / `True`
- Dataset fingerprint unchanged: `True`
- Safety execution is intercepted and counted; any call makes the case fail.
