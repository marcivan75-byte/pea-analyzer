# SECTOR_ROTATION V2.0 — FINAL EXECUTION GUARDRAILS

`src/v182/features/sector_rotation_v2_final.py` is the authoritative execution layer used by the daily Shadow runner.

It intentionally wraps the traceable V2 core instead of silently changing historical development logic.

## 1. Missing evidence is not neutral evidence

A factor family that has no actual source observation is represented as **missing**, not as an available score of 50.

Consequences:

- it does not contribute to `RLS_coverage`;
- the RLS result is shrunk toward neutral according to actual factor coverage;
- DQS remains an independent confidence gate;
- a sparse sector cannot obtain a high-conviction decision by accumulating synthetic neutral components.

A display score may remain neutral when no evidence exists, but the evidence-coverage calculation remains explicit.

## 2. Correction risk and correction alert are different objects

`AVCR` measures vulnerability to a correction.

`CORRECTION_ALERT` means that this vulnerability is beginning to materialize.

Therefore static evidence such as:

- expensive valuation;
- technical overextension;
- crowding;

can produce `PROMISING_BUT_OVERVALUED`, `NO_CHASE` and other caution warnings, but **cannot by themselves create `CORRECTION_ALERT`**.

A correction alert additionally requires at least one dynamic confirmation family:

- breadth deterioration;
- material RLS deterioration;
- weak market confirmation;
- material fundamental deterioration versus the prior snapshot.

The configured minimum number of independent warning families must still be met.

## 3. New position and existing position remain separate

A promising but expensive leader may produce:

- new position: `NO_CHASE`;
- existing position: `HOLD_MONITOR`.

Only a sufficiently confirmed correction alert or rotation-out state can escalate the existing-position output to `EXIT_REVIEW`.

No automatic sell order is emitted.

## 4. Re-entry treats zero as a real score

A genuine sub-score of zero is not interpreted as missing.

This matters for normalized technical-extension and volatility-risk inputs after a correction. The re-entry engine uses explicit `None` handling and preserves valid zero observations.

## 5. Production authority

The final execution layer remains:

- `SHADOW_ONLY`;
- Action score influence = 0;
- ETF score influence = 0;
- automatic BUY authority = 0;
- automatic SELL authority = 0;
- real-order authority = 0.

Promotion still requires dedicated PIT/OOS evidence versus V1 and the no-rotation baseline.

## 6. Per-decision Committee context

Sector Rotation V2 is attached to the current Committee universe through the separate diagnostic output:

`outputs/committee_master/COMMITTEE_SECTOR_ROTATION_V2_CONTEXT.csv`

The join is intentionally non-invasive:

- `COMMITTEE_DECISIONS.csv` is not modified by the Sector V2 context layer;
- row count and `(asset_class, horizon, isin)` ordering are guarded;
- Action sector context comes only from the governed enriched master (`sector_yf` / `sector_yahoo`);
- ETF sector context is allowed only for explicit single-sector buckets already present in Committee data;
- `ETF MULTISECTORIEL / PAYS` is explicitly classified `NO_SINGLE_SECTOR_CONTEXT` rather than receiving a fabricated sector;
- Action theme context uses only governed high-confidence `DIRECT_INDUSTRY` tags;
- low-confidence AI, data-center, grid and cyber hypotheses remain disabled;
- PIT/OOS status and holdout lock are copied into every context row;
- decision, score, sizing and stop-loss influence remain exactly `0.0`.

The companion audit is:

`outputs/audit/SECTOR_ROTATION_V2_DECISION_CONTEXT.json`

A non-zero Sector V2 influence or an unexpected promotion request is reported as `GOVERNANCE_BREACH_BLOCKED` and is never converted into execution authority.

## 7. Theme mapping-gap semantics

The theme mapping worklist now distinguishes governed coverage from true gaps. An Action is not reported as `MAPPING_REQUIRED` when it is already covered by either:

- an effective-dated governed manual mapping; or
- a governed high-confidence direct industry classification.

This does **not** convert a direct industry tag into a revenue-exposure estimate. It only prevents already classified instruments from being incorrectly counted as entirely unmapped.
