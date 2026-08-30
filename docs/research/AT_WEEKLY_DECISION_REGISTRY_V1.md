# AT Weekly Decision Registry V1

Research branch only: `research/at-weekly-v1-20260829`.

This registry prevents regression and accidental re-optimisation of already validated decisions. A LOCKED item may only be reopened by an explicit new decision backed by a validated research run.

| ID | Status | Decision | Evidence / rationale |
|---|---|---|---|
| ENTRY-01 | LOCKED | Entry models `OPT_CONT_07_05` and `OPT_CONT_15_12` are frozen during exit/risk research. | Prior validated entry optimisation; exit research must not change entry weights/thresholds. |
| EXEC-01 | LOCKED | Strategic exit signal uses completed-week information only and executes at next-week open. | Anti-lookahead contract. |
| STOP-01 | LOCKED | Standing fixed protective stop = 9% from entry price. Gap-through fills at actual open; otherwise stop touch fills at stop price. | V7 validated run 33290230246; removes catastrophic tail in tested sample while preserving PF materially better than 5/7%. |
| ENDPOINT-01 | LOCKED | `ENDPOINT_MARK` is valuation only, never an execution. | Censoring-bias correction. |
| FP-01 | LOCKED-FOR-E-STUDY | Early false-positive block remains unchanged while Block E is studied. | Isolate marginal impact of overheating criteria. |
| D-01 | LOCKED-ANCHOR-FOR-E-STUDY | V8 best validated reversal architecture is the comparison anchor for Block E: FULL family, activation 5%, confluence score 1, daily-drop threshold 4%. | V8 run 33290627196, 180 models, SUCCESS. Combined ALL: 208 trades, win 34.62%, mean 2.544%, PF 1.533, RR 2.854, P10 -9.0%, max loss -9.786%. Robust minima: PF 1.341, mean 1.758%, RR 2.854, P10 -9.0%. |
| E-01 | ACTIVE WIP | Optimise overheating criteria and their weights, then measure incremental impact versus D-01 on take-profit quality, net return, PF, RR, tails, and winner preservation. | Current WIP=1. |

## Block architecture

A. Protective risk stop — LOCKED.
B. Early false-positive invalidation — LOCKED for current experiment.
C. Winner activation state — inherited from D-01 anchor for current E study.
D. Confirmed reversal / dynamic take-profit — D-01 anchor frozen during E study.
E. Overheating — ACTIVE research block.

## Block E candidate evidence

At minimum test independently and jointly:
- RSI 75/80/85 and extreme-zone persistence;
- stochastic K 75/80/85 and persistence;
- fresh 52-week high / breakout above prior 52-week high (prior-window only, no lookahead);
- extension above upper Bollinger band;
- distance above SMA20 and SMA50;
- high/peaking ADX with +DI context;
- acceleration/extension of price over recent completed weeks.

Overheating alone is not assumed to be an automatic sell. Compare three behaviours: observation only / lower reversal-confirmation requirement / direct take-profit only for extreme weighted overheating. Selection is empirical.

## Required metrics for Block E

For every candidate: trade count, win rate, mean/net return before fees, PF, RR, P10, avg win/loss, max loss, 12/18/24/36M robustness, number of E-triggered exits, peak unrealised gain, realised gain, gain giveback from prior peak, and post-exit missed upside where measurable without lookahead contamination.

Target RR >4 remains a target only and must never be claimed unless validated with adequate sample size and robustness.
