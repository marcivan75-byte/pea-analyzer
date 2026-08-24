# CI V22.2.2 — final selection, source-quality and timing gates

V22.2.2 extends V22.2.1 after scoring, confidence and market-entry context. It does not alter the underlying scoring formulas, criteria weights or reference scores.

## Base effective selection rules

An instrument first passes the existing V22.2.2 gates:

- selection score >= 77;
- market-adjusted CI confidence score >= 66;
- Actions only: analyst-consensus upside >= 20%;
- ETFs are explicitly exempt from the analyst-consensus-upside gate.

Thresholds are inclusive. Missing Action analyst-consensus upside is fail-closed. A technical potential to the 52-week high can never substitute for analyst consensus.

## Boursorama — Action selection-quality gate

Boursorama is collected only after upstream preselection and cannot create a candidate.

For Actions:

- `STRONG_BUY` or `BUY`: quality gate `PASS`;
- `HOLD`: `WAIT`, not immediately actionable;
- `SELL` or `STRONG_SELL`: `REJECT` from the actionable shortlist;
- missing/unavailable consensus: `REVIEW_SOURCE_MISSING`, never interpreted as a bearish signal.

Analyst count, median target/upside and revision indicators are retained as evidence/context. They do not introduce a new hard threshold and do not overwrite the reference score or the pre-existing consensus-upside measure.

For ETFs, the Action analyst-consensus gate does not apply. Boursorama ETF data remain contextual enrichment only.

## Investing — multi-horizon entry/exit timing gate

Investing is also post-selection only. The timeframe follows the existing horizon contract:

- TCT -> DAILY;
- CT -> WEEKLY;
- MT -> MONTHLY.

Signals are interpreted as follows:

- `STRONG_BUY`: strong entry confirmation;
- `BUY`: entry confirmation;
- `NEUTRAL`: wait / no new entry;
- `SELL`: block new entry + exit review if already held;
- `STRONG_SELL`: block new entry + strong exit review if already held;
- missing/unavailable signal: wait for source, never interpreted as `SELL`.

Investing cannot create an upstream candidate. `BUY`/`STRONG_BUY` can only confirm an instrument that has already passed the base and Boursorama quality gates and whose V22.2.1 technical/market trigger is `READY_FOR_REVIEW`.

## Effective V22.2.2 outputs

The final CI output exposes, in addition to the untouched reference score:

- `CI_BOURSORAMA_GATE` and `CI_BOURSORAMA_REASON`;
- Boursorama consensus/analyst/target/revision source fields when available;
- `CI_INVESTING_SIGNAL` and `CI_INVESTING_SCORE`;
- `CI_INVESTING_ENTRY_GATE`;
- `CI_INVESTING_EXIT_GATE`;
- `CI_EFFECTIVE_ENTRY_STATE_V22_2_2`;
- `CI_EFFECTIVE_EXIT_STATE_V22_2_2`;
- `CI_TIMING_REASON`;
- direct/fallback Boursorama and Investing URLs for auditability.

## Market orientation

The V22.2.1 lightweight orientation remains unchanged and independent of WAVE09:

- FRED: `VIXCLS` only;
- CNN Fear & Greed Index;
- VSTOXX (`V2TX`).

## Runtime/source policy

- source enrichment is limited to upstream-prescreened instruments, maximum 40 unique instruments;
- Boursorama dynamic TTL: 8 h; deep TTL: 168 h;
- Investing TTL: 6 h;
- Boursorama Action/ETF branches share the provider-wide start limiter and in-flight cap;
- Boursorama and Investing source branches overlap safely;
- no broad-universe source scrape is introduced.

## Governance

- WAVE09 remains disabled.
- Base selection scores are not overwritten.
- Reference-score source influence is 0.0.
- Base criteria and weights are unchanged.
- Source gates can change the effective post-selection/actionable state only.
- Neither source can create a candidate.
- Missing source data are explicit and never converted into negative signals.
- T1/T2 remain ACTION TCT only.
- Real orders remain disabled.
