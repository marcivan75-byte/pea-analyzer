# AT Weekly Growth Potential PIT Study Specification V1

Research branch only: `research/at-weekly-v1-20260829`.

## Objective

Measure whether information that was genuinely available at each potential/actual entry date can discriminate future trade quality. The study must never reconstruct historical analyst upside using a current Boursorama target or any other value published after the signal date.

## Core distinction

Two variables are deliberately separate:

- `CONSENSUS_UPSIDE_PIT_PCT`: only populated when a dated analyst-consensus target is demonstrably available at or before the signal date. Otherwise `NA`.
- `TECH_GROWTH_SCORE_PIT_V1`: a reproducible technical growth-potential proxy derived only from completed-week OHLCV/indicators known at the signal date. It is not an analyst target and must never be presented as one.

The repository currently contains no validated historical Boursorama analyst-target series, therefore V1 must leave `CONSENSUS_UPSIDE_PIT_PCT` null rather than fabricate it.

## Locked architecture

This study must use the already locked entry models and exit architecture without changing their parameters:

- entries: `OPT_CONT_07_05`, `OPT_CONT_15_12`;
- standing protective stop: 9%;
- early false-positive block: locked;
- D-01 confirmed reversal: locked anchor;
- Block E: diagnostic only, not an execution trigger;
- trailing reversal: rejected;
- completed-week signals only;
- strategic execution at next-week open;
- `ENDPOINT_MARK` is never an execution.

## PIT technical features

At each entry signal date, using only data available through that completed week:

1. `upside_to_prior_52w_high_pct`: distance from signal close to the highest close/high known before the current signal week. Prior-window reference must use `shift(1)` before rolling.
2. `breakout_above_prior_52w_high_pct`: amount by which signal close exceeds that prior-window high, floor 0.
3. `momentum_12w_pct`: completed-week 12-week return.
4. `momentum_26w_pct`: completed-week 26-week return.
5. `distance_sma20_pct`.
6. `distance_sma50_pct`.
7. `acceleration_4w_pct`: completed-week four-week return.
8. `rsi14` and `stoch_k` as context/overheating diagnostics, not direct growth estimates.

## Fixed technical growth score

`TECH_GROWTH_SCORE_PIT_V1` is a fixed, non-optimised 0-100 diagnostic score, constructed before looking at trade outcomes. It combines monotonic clipped transforms of 12/26-week momentum, four-week acceleration, breakout/trend extension and prior-52-week positioning. The formula is fixed in code and must be reported in the output. No outcome-based fitting or weight search is allowed in V1.

## Test bench

For executed trades since 2023-01-01, join the PIT feature row from the signal week to the locked trade ledger. Report trade count, win rate, mean return, average win, average loss, reward/risk, profit factor, P10 and max loss by:

- technical growth-score bands: `<40`, `40-55`, `55-70`, `70-85`, `>=85`;
- prior-52-week upside bands: `<=0` (already at/above prior high), `0-10`, `10-20`, `20-30`, `>30`;
- 12-week momentum bands: `<0`, `0-10`, `10-20`, `20-30`, `>30`.

Also report results by calendar quarter to detect regime dependence. Every bucket must expose its sample size; no bucket with fewer than 20 realised trades may be called robust.

## Analyst consensus historical data policy

A future enrichment may populate historical analyst consensus only when the source provides a date-stamped value that can be proven to have existed at or before the signal date. Current targets scraped today may be stored for forward use but may not be backfilled into 2023-2026 history.

Potential sources can be assessed separately. Alpha Vantage documents historical EPS/revenue estimate and revision information, which may become a PIT fundamental input, but this is not equivalent to historical Boursorama target-price consensus. Any such enrichment requires its own source/date audit before use.

## Deliverables

- JSON full study;
- CSV trade-level PIT diagnostic ledger;
- CSV bucket summary;
- Markdown concise interpretation;
- explicit coverage field for real historical consensus (expected 0% in V1 unless a validated source is added before execution).

## Validation gates

- zero current/future consensus leakage;
- prior-52-week reference uses only prior completed weeks;
- all feature timestamps are <= signal timestamp;
- fixed score formula, no outcome fitting;
- locked entries/exits unchanged;
- endpoint marks excluded from realised performance metrics;
- bucket samples always shown;
- results are diagnostic unless minimum sample and temporal consistency support a stronger conclusion.
