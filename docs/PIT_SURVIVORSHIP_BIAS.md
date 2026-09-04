# PIT historical universe — survivorship-bias remediation

## Objective

A backtest must never derive its investable universe from securities that still
exist today.  For every historical decision date it must reconstruct the set of
securities that were actually listed, identifiable and eligible at that date.

This is an independent data layer from OHLCV.  Good price history for current
securities does not solve survivorship bias.

## Required temporal model

### 1. Security identity

Use a stable `security_id`.  A ticker is an alias, not a permanent identifier.
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

### 3. Terminal/corporate events

Every disappearance must be explicit where known:

- delisting
- cash acquisition
- stock acquisition / merger
- bankruptcy / liquidation
- loss of eligibility
- security replacement / restructuring

Recommended fields:

- `security_id`
- `effective_date`
- `event_type`
- `cash_per_share`
- `successor_security_id`
- `exchange_ratio`
- `source`
- `confidence`

A disappeared security must never simply vanish from a simulated portfolio.

## Backtest treatment

1. IPO: no presence before first eligible trading date.
2. Ordinary delisting: position remains until the effective/last tradable date;
   no indefinite forward-fill.
3. Cash acquisition: settle using documented cash consideration when available.
4. Stock merger: convert using documented successor and exchange ratio when
   available.
5. Bankruptcy: preserve the economic loss; never drop the security because its
   later price history is absent.
6. Ticker change: preserve the same stable identity unless the underlying
   security genuinely changed.
7. Eligibility loss: security cannot be selected after the effective date, but
   an already-held position follows the strategy/legal exit rule explicitly.

## Reconstruction workflow

### Pass A — enumerate historical securities

Build a superset from historical exchange/instrument lists, historical index
constituents and corporate-action/delisting archives.  Current listings are only
one component of that superset.

### Pass B — identity resolution

Resolve ticker and ISIN histories into stable `security_id` records.  Duplicate
or ambiguous identities remain in quarantine.

### Pass C — membership intervals

Create effective-dated PEA/universe intervals.  Where exact historical PEA
eligibility cannot be proven, store a reconstructed/proxy interval with a lower
confidence instead of silently treating it as fact.

### Pass D — OHLCV and terminal events

Backfill prices for identified non-survivors and attach their terminal event.
Missing terminal outcomes are separately reported.

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
Unknown membership is fail-closed.  Results may be labelled survivorship-control
certified only when coverage thresholds are met.

### `PIT_ESTIMATED`

Allows reconstructed memberships below strict confidence, but must report their
share and run a sensitivity analysis.

### `SURVIVOR_ONLY`

Uses today's universe.  Diagnostic only.  It must never be presented as a
validated historical performance result.

## Proposed acceptance gates

For the principal 2010–2026 research base:

- 100% temporal identity integrity for securities included in a strict run;
- >= 99% OHLCV coverage by historical-universe member-date after reconstruction;
- >= 99% explicit terminal-event coverage for known disappeared securities;
- 0 silent disappearance of a held security;
- 0 use of a current-universe list to determine past membership;
- separate unresolved/quarantine register;
- mandatory comparison of `SURVIVOR_ONLY` versus `PIT_STRICT`/`PIT_ESTIMATED`.

The final comparison must publish deltas in CAGR/annual return, maximum drawdown,
win rate, profit factor and the strategy's principal selection metrics.  This
quantifies the actual survivorship-bias contribution rather than merely assuming
it is small.

## First implementation

`src/v182/backtest/pit_universe.py` provides the dependency-light core:

- stable identity structures;
- effective-dated membership rows;
- explicit terminal-event structures;
- `universe_as_of()` selection;
- interval integrity validation;
- date-level price-coverage reporting.

Next integration step is to populate the historical identity/membership/event
ledgers from the base/reference sources and make the main backtest engines call
`universe_as_of()` instead of deriving candidates from present-day symbols.
