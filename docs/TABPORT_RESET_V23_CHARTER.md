# TABPORT RESET V23 — Benchmark-first charter

## Status

V22.1 is closed as a failed autonomous stock-picking strategy. Its 2023–2026 holdout is now observed and MUST NOT be used to select, tune, weight, threshold, rank, or promote any V23 rule.

## Objective

V23 exists only if it demonstrates net value versus passive PEA benchmarks. The target is not an arbitrary return. The target is robust alpha and/or materially better risk-adjusted performance after realistic costs.

## Non-negotiable governance

- WIP=1: one active research hypothesis at a time.
- PIT only; strict anti-lookahead; fail-closed on missing PIT inputs.
- 26-week embargo wherever 26-week outcomes are used.
- 2023–2026 is forbidden for model selection because it is contaminated by prior observation.
- No repeated reuse of one validation window across successive optimization passes.
- Hyperparameter/variant search must be explicitly counted and kept small.
- Overlapping 26-week observations must not be treated as independent evidence.
- Every candidate must be compared with a passive benchmark after fees.
- No criterion survives merely because it reduces stops; it must improve economic value.
- MAE/MFE remain reporting diagnostics unless a future, clean training protocol explicitly authorizes their use.

## Architecture: maximum four economic blocks

1. Trend.
2. Momentum.
3. Volatility / downside risk.
4. One genuinely different PIT information source: fundamental, valuation, revisions/consensus, or another source proven not to be a price transformation.

Price-derived variants that are algebraic rewrites of the same information do not count as independent blocks.

## Research protocol

### Stage A — Baselines first

Before any stock-picking research, establish passive and simple active baselines on the same capital, calendar, costs and execution assumptions.

Required comparisons:
- broad PEA passive benchmark(s), total-return where available;
- simple trend/momentum baseline with very few rules;
- V22.1 frozen, for historical reference only.

### Stage B — One hypothesis at a time

For each proposed block:
1. state the economic hypothesis before testing;
2. define one primary metric and guardrails;
3. test only a small predeclared family of variants;
4. use chronological development/calibration/evaluation windows that are not recycled for subsequent selection;
5. report effective time sample, not only row count;
6. reject the block if the improvement is small, unstable, or benchmark-dependent.

### Stage C — Combination

Blocks may be combined only after each has shown standalone incremental value. Combination must be additive and parsimonious. No ensemble, adaptive regime, or micro-weighting layer is allowed unless it produces a large, stable incremental gain on an untouched pre-2023 evaluation period.

## Promotion rules

A candidate cannot be promoted merely for positive absolute return. It must satisfy all of the following on untouched evaluation data:

- positive net alpha versus the designated passive benchmark;
- acceptable drawdown relative to the benchmark;
- robustness across distinct chronological subperiods;
- no dependence on one exceptional year or a few trades;
- no material degradation under fee/slippage stress;
- sufficiently large effect to justify added complexity.

If a simpler model is statistically/economically indistinguishable from a complex one, the simpler model wins.

## What V23 explicitly forbids

- tuning on 2023–2026;
- optimizing the stop rate as a primary objective;
- treating correlated technical transforms as independent evidence;
- repeated validation-window mining;
- retrospective monthly/yearly ranking using future signals;
- claiming robustness from tens of thousands of overlapping rows;
- adding complexity for marginal metric improvements.

## First active mission

Build the benchmark-first diagnostic. Freeze common assumptions (EUR 65,000 initial capital, realistic fees/slippage, integer shares where relevant), identify the passive reference series available in the repository, and quantify whether any simple pre-2023 rule adds stable net value over passive exposure without consulting 2023–2026 for selection.

Only after this baseline is audited may a single stock-picking hypothesis be opened.