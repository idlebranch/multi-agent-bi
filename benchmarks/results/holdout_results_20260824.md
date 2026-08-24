# Final Sprint Holdout Results

- Source commit: `b4f2a6fe0085c05ef39048d19d9abeedc4845f8b`

These holdouts are reported separately and are not included in the frozen 90-business / 25-safety benchmark denominator.

| Suite | Passed | Total | Rate |
|---|---:|---:|---:|
| safety | 12 | 12 | 100.00% |
| numerical | 6 | 6 | 100.00% |
| representation | 5 | 5 | 100.00% |

- Safety holdouts verify rejection precedes out-of-domain classification and that no database execution path is entered.
- Numerical holdouts target percent-scale fidelity without changing SQL or Agent prompts.
- Representation holdouts accept equivalent two-column and three-column quarter outputs.
- No LLM or database call is made by this deterministic holdout runner.
