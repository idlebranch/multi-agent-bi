# Multi-Agent BI Benchmark Baseline

- Timestamp (UTC): 2026-08-24T07:12:47.511765+00:00
- Commit: `13fe742b1054c47ccf314d98742864a53b3a2e04`
- Benchmark version: `1.0.0`
- Model: `deepseek-chat`
- Business cases: 90
- Safety cases: 25
- Database unchanged: **True**

## Core metrics

| Metric | Result |
|---|---:|
| Overall Execution Accuracy | 78.82% (67/85) |
| Overall Answer Accuracy | 70.00% (63/90) |
| End-to-End Accuracy | 64.44% (58/90) |
| Safety Blocking Rate | 80.00% (20/25) |

## Execution Accuracy by difficulty

| Difficulty | EX | E2E |
|---|---:|---:|
| Easy | 92.31% (24/26) | 81.48% (22/27) |
| Medium | 68.57% (24/35) | 50.00% (19/38) |
| Hard | 79.17% (19/24) | 68.00% (17/25) |

## Results by category

| Category | EX | E2E |
|---|---:|---:|
| ambiguity | unavailable | 66.67% (2/3) |
| complex_filter | 71.43% (5/7) | 71.43% (5/7) |
| empty_result | 100.00% (3/3) | 100.00% (3/3) |
| filtering_sorting | 80.00% (8/10) | 70.00% (7/10) |
| governed_metric | 83.33% (10/12) | 75.00% (9/12) |
| multi_table_join | 81.25% (13/16) | 56.25% (9/16) |
| out_of_domain | unavailable | 100.00% (2/2) |
| ratio_metric | 75.00% (6/8) | 75.00% (6/8) |
| single_table_aggregation | 81.82% (9/11) | 81.82% (9/11) |
| time_series | 70.00% (7/10) | 20.00% (2/10) |
| time_window | 75.00% (6/8) | 50.00% (4/8) |

## Reliability

- Average latency: 4.017 s
- P50 latency: 4.104 s
- P95 latency: 9.464 s
- Average repair count: 0.1333
- Reviewer rejection rate: 15.31% (15/98)
- Observed LLM-stage calls (lower bound): 405
- Exact provider request count: unavailable (provider metadata is not retained in workflow state)
- Token usage: unavailable (provider usage metadata is not retained in workflow state)

## Failure taxonomy

| Failure category | Count | Share of failed cases | Examples |
|---|---:|---:|---|
| answer_synthesis | 13 | 40.62% | B013, B024, B027, B031, B037 |
| wrong_table | 4 | 12.50% | B007, B029, B035, B044 |
| filter | 4 | 12.50% | B019, B020, B076, B079 |
| metric_definition | 4 | 12.50% | B050, B051, B062, B063 |
| aggregation | 3 | 9.38% | B009, B070, B075 |
| time_logic | 2 | 6.25% | B042, B045 |
| missing_join | 1 | 3.12% | B034 |
| ambiguity_handling | 1 | 3.12% | B085 |

## Database protection

- Before SHA-256: `7e7bcc7503ec63cb4ae92597d5d96f0e2ad9eb718805889d43958e2697b064dc`
- After SHA-256: `7e7bcc7503ec63cb4ae92597d5d96f0e2ad9eb718805889d43958e2697b064dc`
- Integrity before/after: `ok` / `ok`
- Foreign-key violations before/after: 0 / 0
- Safety execution is intercepted and counted; any call makes the case fail.
