# PIT historical universe — survivorship-bias remediation

## Objective

A backtest must never derive its investable universe from securities that still
exist today. For every historical decision date it must reconstruct the set of
securities that were actually listed, identifiable and eligible at that date.

This is an independent data layer from OHLCV. Good price history for current
securities does not solve survivorship bias.

## Required temporal model

### 1. Security identity

Use a stable `security_id`. A ticker is an alias, not a permanent identifier.
Recommended fields:

- `security_id`
- `issuer_id`
- `isin`
- `ticker`
- `mic`
- `valid_from`
- `valid_to`
- `source`
- `source_asof_date`

Ticker/ISIN changes must not silently create or delete an economic history.

### 2. Universe membership

Minimum fields:

- `security_id`
- `universe_code`
- `effective_from`
- `effective_to`
- `eligible`
- `reason`
- `source`
- `source_asof_date`
- `confidence`

The backtest universe on date D is the set of membership intervals containing D,
not the current reference universe.

### 3. Listing lifecycle and terminal events

A market delisting is not automatically an economic terminal event. A security
can be delisted from one venue and transferred to another while keeping the same
economic identity. Venue events and economic terminal events are therefore stored
separately.

Listing events include admission, delisting, market transfer, suspension and
resumption. Economic terminal events include cash acquisition, stock acquisition,
merger, bankruptcy, liquidation and security cancellation.

A disappeared security must never simply vanish from a simulated portfolio.
Ambiguous exit evidence is quarantined instead of being converted into a guessed
loss or guessed continuation.

## Backtest treatment

1. IPO: no presence before first eligible trading date.
2. Market transfer: preserve stable identity when supported by the source.
3. Ordinary delisting: position remains until the documented effective/last
   tradable date; no indefinite forward-fill.
4. Cash acquisition: settle using documented cash consideration when available.
5. Stock merger: convert using documented successor and exchange ratio when
   available.
6. Bankruptcy: preserve the economic loss; never drop the security because later
   price history is absent.
7. Ticker change: preserve the same stable identity unless the underlying security
   genuinely changed.
8. Eligibility loss: security cannot be selected after the effective date, while
   an already-held position follows the explicit strategy/legal exit rule.

## Reconstruction workflow

### Pass A — enumerate historical securities

Build a superset from historical exchange/instrument lists, historical index
constituents and corporate-action/delisting archives. Current listings are only
one component of that superset.

### Pass B — identity resolution

Resolve ticker and ISIN histories into stable `security_id` records. Duplicate or
ambiguous identities remain in quarantine.

### Pass C — membership intervals

Create effective-dated PEA/universe intervals. Where exact historical PEA
eligibility cannot be proven, store a reconstructed/proxy interval with a lower
confidence instead of silently treating it as fact.

### Pass D — OHLCV and terminal events

Backfill prices for identified non-survivors and attach their documented terminal
or transfer event. Missing economic outcomes are separately reported.

### Pass E — certification audit

For each test date report at least:

- historical universe count
- number of known non-survivors
- membership-confidence coverage
- OHLCV coverage
- terminal-event coverage
- unresolved identities/events

## Certification modes

### `PIT_STRICT`

Only confirmed memberships meeting the strict confidence threshold are used.
Unknown membership is fail-closed. Results may be labelled survivorship-control
certified only when all blocking gates are met.

### `PIT_ESTIMATED`

Allows reconstructed memberships below strict confidence, but must report their
share and run a sensitivity analysis.

### `SURVIVOR_ONLY`

Uses today's universe. Diagnostic only. It must never be presented as a validated
historical performance result.

## Blocking acceptance gates

For the principal 2010–2026 research base:

- 100% temporal identity/membership structural integrity for the strict set;
- >= 99% OHLCV coverage by historical-universe member-date;
- >= 99% explicit terminal-event coverage for known disappeared securities;
- 0 unresolved historical disappearance;
- 0 quarantined exit evidence in a strict-certified run;
- 0 silent disappearance of a held security;
- 0 use of a current-universe list to determine past membership;
- separate unresolved/quarantine register;
- mandatory comparison of `SURVIVOR_ONLY` versus `PIT_STRICT`/`PIT_ESTIMATED`.

`src/v182/backtest/pit_universe.py::strict_certification_status()` enforces these
blocking gates in code and returns only `PIT_STRICT_CERTIFIED` when every gate is
satisfied. The default is fail-closed.

The final comparison must publish deltas in CAGR/annual return, maximum drawdown,
win rate, profit factor, expectancy and the strategy's principal selection
metrics. This quantifies the actual survivorship-bias contribution rather than
assuming it is small.

## Source hierarchy

The historical-universe reconstruction uses authoritative evidence independently
from price vendors:

- Euronext Cash Market Notices as primary historical listing/delisting/corporate-
  action evidence, particularly before MiFID II;
- ESMA FIRDS as primary reference/termination evidence from the MiFID II era;
- Euronext security pages as cross-check;
- AMF/issuer documentation for economic terms of mergers, squeeze-outs,
  liquidations and related events;
- separate historical evidence for PEA eligibility: listing status alone must not
  be treated as proof of PEA eligibility.

Yahoo/Boursorama may supply price/reference observations but cannot, by
themselves, certify historical membership or the economic reason for a security's
disappearance.

## Implementation status

Implemented and CI-covered:

- stable identity and effective-dated universe membership;
- listing lifecycle separated from economic terminal events;
- strict fail-closed universe selection;
- structural interval validation;
- historical price-coverage reporting;
- authoritative source registry;
- exit-evidence normalization and quarantine;
- benchmark survivorship audit for historical members absent from the latest
  snapshot;
- blocking `PIT_STRICT` certification function and tests.

The code infrastructure is therefore finalized. The 2010–2026 historical database
itself must still be populated/certified against these gates before any result may
be labelled `PIT_STRICT_CERTIFIED`. Absence of populated evidence is not converted
into an optimistic certification.

## Authorization for +25% retro-engineering

The +25% winner-pattern research may run in diagnostic mode while reconstruction
continues, but final model selection/validation must use a certified PIT universe.
It must also compare winners with matched non-winners from the same historical
dates/regimes to control outcome-selection bias. Final publication must compare
`SURVIVOR_ONLY`, `PIT_ESTIMATED` and, when gates pass, `PIT_STRICT`.
