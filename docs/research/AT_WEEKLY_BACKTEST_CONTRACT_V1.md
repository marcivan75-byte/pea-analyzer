# AT WEEKLY BACKTEST CONTRACT V1

Status: LOCKED for the research/at-weekly-v1-20260829 branch unless explicitly changed by decision.

## Fixed entry models
- OPT_CONT_07_05 and OPT_CONT_15_12 remain fixed during exit/risk-control research.
- No entry weights or thresholds may be changed while this contract is active.

## Strategic weekly exits
- RSI, stochastic, PSAR, moving-average and other strategic exit signals use completed-week data only.
- Strategic exit execution occurs at the next week's open.
- No signal may use information from an unfinished future bar.

## Protective fixed stop-loss
- A protective stop is independent from strategic indicators and is active immediately after entry.
- Stop level is known ex ante: entry_price * (1 - stop_pct/100).
- For a weekly bar while the position is active:
  1. If weekly open <= stop level, fill at the actual weekly open (gap-through).
  2. Else if weekly low <= stop level, fill at the stop level.
  3. Else the stop is not triggered.
- A nominal -5% stop therefore normally exits at -5%, except for a genuine gap-through below the stop.
- A result materially below the nominal stop must be traceable to an actual opening gap or be rejected as a simulation/data defect.

## Trailing stops
- A trailing stop may not be raised from a same-bar high and then assumed hit by that same bar low when intrabar ordering is unknown.
- Trailing levels must be based on information available before the tested bar, or on finer PIT-safe data that establishes sequence.

## Endpoint accounting
- ENDPOINT_MARK is mark-to-market evaluation only.
- ENDPOINT_MARK is never an execution.

## Required audit ledger
Every trade-level audit must retain at least:
- symbol
- entry model
- entry date and price
- exit date and price
- return
- exit reason
- protective stop level when applicable
- gap-open reason when applicable
- endpoint flag

## Validation gate
No PF, reward/risk, win rate or mean-return result may be treated as reliable unless:
- protective-stop contract tests pass;
- anti-lookahead controls pass;
- fixed entry models are unchanged;
- worst-tail trades are auditable;
- sample-size safeguards are respected.

## Research-only
This contract and all associated runs are research-only and must not place orders or modify production behavior.
