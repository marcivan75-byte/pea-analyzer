# BACKTEST_OPTIMIZER_V1

## Objective

Optimise committee criterion weights using **point-in-time historical snapshots** and an explicit out-of-sample guard. The module is advisory: it never writes production weights.

## Why this architecture

A valid backtest must use only information that existed on the signal date. Rebuilding past consensus, fundamentals, Fear & Greed, whales/insiders or other criteria from today's values would introduce look-ahead bias. The optimiser therefore consumes archived committee snapshots and derives realised forward returns only from later archived prices.

## Baseline model

The default baseline expands the V20.4 3609 committee formula into effective auditable influences:

- V10 block: 25.00%
- momentum: 15.75%
- quality: 12.50%
- catalyst / analyst-consensus family: 9.50%
- risk: 9.25%
- value: 8.75%
- expectancy: 7.50%
- structure: 6.25%
- sector: 3.00%
- fiscal: 2.50%

Optional point-in-time directional scores (`technical`, `fear_greed`, `smart_money`, `decision_overlay`) start at zero and can receive weight only when the corresponding score column actually exists in the archived history. Each optional block is capped at 12% by default.

## Optimisation process

1. Load dated snapshots.
2. Match the same instrument to a later archived price near the configured horizon.
3. Remove incomplete dates and enforce a minimum cross-section.
4. Split chronologically into train and holdout periods.
5. Evaluate thousands of bounded candidate weight vectors on the training set.
6. Shrink the raw optimum back toward current production weights.
7. Validate on the untouched holdout period.
8. Recommend new weights only if the out-of-sample return improves without excessive drawdown or parameter drift.

## Default guards

- 12 eligible snapshots minimum.
- 4 holdout snapshots minimum.
- 30 instruments minimum per snapshot.
- top 25 simulated candidates.
- 12 bps turnover cost.
- 35% maximum weight on a standard block.
- 12% maximum on a new optional block.
- maximum L1 weight drift 55%.
- minimum holdout mean-return improvement 25 bps per evaluation period.
- maximum drawdown worsening 3 percentage points.

## Outputs

`outputs/backtest_optimizer/` contains:

- `SUMMARY.md` — committee-readable decision summary.
- `WEIGHTS.csv` — current versus robust recommended weights.
- `SENSITIVITY.csv` — ±20% one-factor sensitivity tests.
- `LEADERBOARD_TOP250.csv` — best training configurations.
- `AUDIT.json` — data sufficiency, guardrails and acceptance reasons.

`INSUFFICIENT_HISTORY` is a valid result and must not be bypassed. It means the archive is not yet deep enough to support a robust weight change.

## Evolution path

V1 is a global weight optimiser. Once enough history exists across distinct regimes, V2 can add walk-forward regime segmentation (risk-on, risk-off, high volatility, low volatility) and dynamic weights without changing the point-in-time discipline.
