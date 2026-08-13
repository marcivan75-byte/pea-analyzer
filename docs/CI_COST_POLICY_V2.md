# CI cost policy V2 — no degradation

## Non-negotiable rule
Cost optimisation must never reduce the validation depth of the final PR head.

The final commit proposed for merge must pass the same complete validation contract:
- compile all Python source and tests;
- Ruff;
- static Python safety audit;
- full referential/governance integrity checks;
- full pytest suite, including ETF, TCT, Gold, provenance, collection and Committee tests.

## Cost control
Intermediate development commits may use a GitHub-supported skip-CI marker. The final checkpoint commit must not use a skip marker and must run the complete CI above.

`concurrency` with `cancel-in-progress: true` remains enabled so an obsolete validation is cancelled when a newer final candidate replaces it.

## Production
Production Committee runs are independent from CI. No backtest is run by the daily Committee. Backtests/walk-forward/calibration are separate manual research processes.

## Quality safeguards
No reduction of criteria, sources, data quality gates, PIT/provenance rules, TCT/ETF/Gold governance, artifact traceability, or test coverage is allowed to reduce runtime or billing.