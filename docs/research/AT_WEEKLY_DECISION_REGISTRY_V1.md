# AT Weekly Decision Registry V1

Research branch only: `research/at-weekly-v1-20260829`.

This registry prevents regression and accidental re-optimisation of already validated decisions. A LOCKED item may only be reopened by an explicit new decision backed by a validated research run.

| ID | Status | Decision | Evidence / rationale |
|---|---|---|---|
| ENTRY-01 | LOCKED | Entry models `OPT_CONT_07_05` and `OPT_CONT_15_12` are frozen during exit/risk research. | Prior validated entry optimisation; exit research must not change entry weights/thresholds. |
| EXEC-01 | LOCKED | Strategic exit signal uses completed-week information only and executes at next-week open. | Anti-lookahead contract. |
| STOP-01 | LOCKED | Standing fixed protective stop = 9% from entry price. Gap-through fills at actual open; otherwise stop touch fills at stop price. | V7 validated run 33290230246; removes catastrophic tail in tested sample while preserving PF materially better than 5/7%. |
| ENDPOINT-01 | LOCKED | `ENDPOINT_MARK` is valuation only, never an execution. | Censoring-bias correction. |
| FP-01 | LOCKED | Early false-positive block remains unchanged during downstream exit research. | V6/V8 architecture retained to isolate marginal exit effects. |
| D-01 | LOCKED-ANCHOR | V8 validated reversal architecture is the comparison anchor: FULL family, activation 5%, confluence score 1, daily-drop threshold 4%. | V8 run 33290627196, 180 models, SUCCESS. Combined ALL: 208 trades, win 34.62%, mean 2.544%, PF 1.533, RR 2.854, P10 -9.0%, max loss -9.786%. Robust minima: PF 1.341, mean 1.758%, RR 2.854, P10 -9.0%. |
| E-01 | LOCKED-NOT-EXECUTION | Overheating remains diagnostic/context evidence but is not retained as an autonomous execution trigger in the next phase. | V10 apparent improvement (`V10_E_1063`: ALL mean 3.276%, PF 1.572, RR 4.425) carried 5.77% endpoint marks and robust RR only 3.684. V11 imposed endpoint share <=3% and tested 700 local variants: 25 passed guards, 0 reached robust RR>=4; best guarded `V11_E_0001` was inferior to D-01 on ALL mean (2.434% vs 2.544%) and PF (1.506 vs 1.533), with only RR +0.072. Therefore no robust material E execution improvement is validated. |
| F-TRAIL-01 | LOCKED-REJECTED | Do not add a 4/5/6% trailing-reversal execution block to the validated architecture. | V12 corrected validation run 33294725049: 90 models tested across 4/5/6% trailing, 6 activation thresholds and 5 reversal confirmations; 0 models passed the locked tail/profit/endpoint/sample guards and 0 reached guarded robust RR>=4. The explicit 5% trailing family produced no guarded candidate. |
| EXIT-LOOP-01 | LOCKED | Exit architecture for the next validation study is A protective stop + B early false-positive invalidation + C winner activation + D confirmed reversal; E is diagnostic only; F trailing is rejected. | Broad exit research V7-V12 found no further material robust improvement after endpoint-censoring and sample safeguards. Target RR>4 was not robustly achieved and is not claimed. |

## Block architecture

A. Protective risk stop — LOCKED.
B. Early false-positive invalidation — LOCKED.
C. Winner activation state — inherited from D-01 anchor.
D. Confirmed reversal / dynamic take-profit — LOCKED comparison anchor.
E. Overheating — LOCKED as diagnostic/context only; not an autonomous execution trigger after V11 guard validation.
F. Trailing reversal — LOCKED-REJECTED after V12.

## Block E evidence retained for diagnostics

- RSI overheating and persistence;
- stochastic overheating and persistence;
- fresh prior-window 52-week high breakout;
- extension above upper Bollinger band;
- distance above SMA20/SMA50;
- high/peaking ADX with +DI context;
- recent completed-week acceleration.

These may be reported and used as explanatory context, but V11 did not validate them as a material robust execution improvement over D-01 under endpoint-censoring safeguards.

## V12 trailing contract and conclusion

- fixed standing protective stop 9% remains independent and active;
- trailing reference used the highest completed-week close known before the current week;
- no same-week future high set a trailing level and was then tested against that same week's low/close;
- trailing signal used completed-week evidence only;
- strategic trailing exit executed at next-week open;
- 5% trailing reversal was explicitly tested, with 4% and 6% sensitivity;
- run 33294725049 validated the test bench itself and returned 0 guarded eligible trailing models;
- therefore trailing reversal is rejected for the locked architecture rather than forced into production research.

Target RR >4 remains a target only and must never be claimed unless validated with adequate sample size and robustness.
