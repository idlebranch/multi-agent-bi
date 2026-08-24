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
| Overall Execution Accuracy | 90.59% (77/85) |
| Overall Answer Accuracy | 88.89% (80/90) |
| End-to-End Accuracy | 88.89% (80/90) |
| Safety Blocking Rate | 80.00% (20/25) |

## Execution Accuracy by difficulty

| Difficulty | EX | E2E |
|---|---:|---:|
| Easy | 96.15% (25/26) | 96.30% (26/27) |
| Medium | 85.71% (30/35) | 84.21% (32/38) |
| Hard | 91.67% (22/24) | 88.00% (22/25) |

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
| ratio_metric | 100.00% (8/8) | 100.00% (8/8) |
| single_table_aggregation | 90.91% (10/11) | 90.91% (10/11) |
| time_series | 90.00% (9/10) | 90.00% (9/10) |
| time_window | 75.00% (6/8) | 62.50% (5/8) |

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
| filter | 2 | 20.00% | B009, B019 |
| wrong_table | 2 | 20.00% | B029, B035 |
| reviewer_false_positive | 2 | 20.00% | B050, B051 |
| wrong_join | 1 | 10.00% | B034 |
| time_logic | 1 | 10.00% | B044 |
| answer_synthesis | 1 | 10.00% | B053 |
| ambiguity_handling | 1 | 10.00% | B085 |

## Evaluator audit

The live Agent was not re-run. Saved outputs were re-evaluated offline after fixing deterministic normalization, output projection, and tie handling.

- Pre-audit EX: 78.82% (67/85)
- Pre-audit Answer Accuracy: 70.00% (63/90)
- Pre-audit E2E: 64.44% (58/90)
- Business decisions changed by evaluator audit: 22
- Gold numeric values were not changed.

## Database protection

- Before SHA-256: `7e7bcc7503ec63cb4ae92597d5d96f0e2ad9eb718805889d43958e2697b064dc`
- After SHA-256: `7e7bcc7503ec63cb4ae92597d5d96f0e2ad9eb718805889d43958e2697b064dc`
- Integrity before/after: `ok` / `ok`
- Foreign-key violations before/after: 0 / 0
- Safety execution is intercepted and counted; any call makes the case fail.
