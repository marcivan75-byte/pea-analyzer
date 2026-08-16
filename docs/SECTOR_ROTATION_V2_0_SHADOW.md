# SECTOR_ROTATION V2.0 — SHADOW SPECIFICATION

## Purpose

SECTOR_ROTATION V2.0 detects emerging sector and theme leadership, separates economic opportunity from valuation/correction risk, and provides an explainable shadow ranking for the PEA Action/ETF process.

The model is deliberately **SHADOW_ONLY** until dedicated PIT/OOS validation demonstrates incremental value versus the existing Sector Rotation V1 baseline.

It must answer four separate questions:

1. Is the sector structurally/cyclically improving?
2. Is the market beginning to confirm the improvement?
3. Has valuation already discounted too much of that improvement?
4. Is the appropriate action to prioritize, wait, avoid chasing, or review an existing position?

## Governance

- Decision influence on Actions: **0**.
- Decision influence on ETF: **0**.
- Real orders: **forbidden**.
- Automatic sales: **forbidden**.
- T1/T2: **forbidden** in Sector Rotation; they remain ACTION TCT only.
- No promotion before dedicated PIT/OOS validation.
- Missing factor families are not treated as positive evidence: scores are shrunk toward neutral and DQS declines.
- Data with inadequate DQS cannot create high-conviction sector decisions.
- Existing Sector Rotation V1 remains the comparison baseline during the V2 shadow phase.

## Core scores

### STS — Structural Trend Score
Long-duration economic trend. It is kept conservative/neutral until governed long-horizon drivers are available.

### CTS — Cyclical Trend Score
Short/medium cycle strength using momentum, acceleration, relative strength and breadth.

### SQS — Sector Quality Score
Current economic quality using breadth, growth and earnings-revision evidence.

### RLS — Rotation Lead Score
Primary anticipatory score.

Initial research weights:

| Family | Weight |
|---|---:|
| Earnings revisions | 18% |
| Breadth | 15% |
| Relative strength | 15% |
| Capital flows | 12% |
| Fundamental acceleration | 12% |
| Macro compatibility | 10% |
| Catalysts | 8% |
| Early price/volume | 5% |
| Internal diffusion | 5% |

The weights are baselines to test, not validated production coefficients.

### MCS — Market Confirmation Score
Measures whether price, relative strength, breadth and diffusion confirm the economic thesis.

### AVCR — Adjusted Valuation & Correction Risk
Separates raw expensive valuation from the extent to which that valuation can be justified by growth, revisions and quality.

Initial AVCR component weights:

| Component | Weight |
|---|---:|
| Valuation vs own history | 18% |
| Valuation vs market | 10% |
| Price/fundamental gap | 15% |
| Technical overextension | 12% |
| Crowding/euphoria | 12% |
| Breadth divergence | 10% |
| Multiple expansion dependency | 10% |
| Expectation fragility | 8% |
| Volatility regime | 5% |

The engine first estimates `raw_VCR`, then computes `valuation_justification`, and derives `AVCR` so that a high-quality/high-growth sector is not punished identically to an equally expensive sector with weak fundamentals.

### DQS — Data Quality Score
Controls confidence using freshness, completeness, PIT quality, source reliability and constituent coverage.

- DQS >= 80: high-conviction shadow decision permitted.
- DQS 65–79: signal/watch permitted; no high-conviction BUY-zone classification.
- DQS < 65: `NO_ACTION_INSUFFICIENT_DATA`.

### RARS — Risk Adjusted Rotation Score
Ranking score combining opportunity, valuation risk and data quality. It is a prioritization tool, not an order signal.

## Required valuation warning

`PROMISING_BUT_OVERVALUED` is mandatory when the sector remains promising while valuation/correction risk is elevated.

Initial condition:

- RLS >= 70, and
- AVCR >= 65.

It must not turn a bullish sector automatically bearish. Typical resulting decisions are:

- `WAIT_FOR_PULLBACK`, or
- `NO_CHASE` when AVCR >= 75.

This directly separates **good sector** from **good entry price**.

## Warning framework

Supported warnings include:

- `PROMISING_BUT_OVERVALUED`
- `TECHNICAL_OVEREXTENSION`
- `CROWDING_EUPHORIA`
- `LEADERSHIP_NARROWING`
- `MULTIPLE_EXPANSION_DEPENDENCY`
- `PERFECTION_PRICED_IN`
- `VALUE_TRAP`
- `CORRECTION_ALERT`
- `BULLISH_BUT_OVEREXTENDED`

`CORRECTION_ALERT` requires independent warning families rather than several correlated technical indicators. The initial rule requires at least three independent families with AVCR >= 65.

## State machine

Primary states:

`ACCUMULATION -> EARLY_ROTATION -> CONFIRMED_ROTATION -> LEADERSHIP -> DISTRIBUTION -> ROTATION_OUT`

`NEUTRAL` is allowed when evidence is insufficient for a directional state.

Transversal warning states such as `BULLISH_BUT_OVEREXTENDED` can coexist with `LEADERSHIP`.

State thresholds are configuration-driven and use history for RLS velocity and breadth change. V2 history is persisted independently from V1.

## New-position actions

The shadow engine may label a sector:

- `PRIORITY_BUY_ZONE`
- `BUY_ZONE`
- `ACCUMULATE_ON_WEAKNESS`
- `WAIT_FOR_PULLBACK`
- `NO_CHASE`
- `NO_NEW_ENTRY`
- `WATCH`
- `AVOID`
- `NO_ACTION_INSUFFICIENT_DATA`

These labels have **zero production order authority** in V2.0 Shadow.

## Existing-position actions

Existing positions are treated separately from new entries:

- `HOLD`
- `HOLD_MONITOR`
- `EXIT_REVIEW`

`NO_CHASE` for a new position is not an automatic sell for an existing position.

## Sector-specific drivers

The configuration reserves sector-specific driver families for:

- semiconductors;
- software;
- data centers;
- electrical grid/equipment;
- defense;
- energy;
- nuclear;
- banks;
- pharma/biotech;
- automotive;
- luxury;
- metals/mining.

These drivers are only active when a governed collector supplies actual point-in-time observations. Their absence lowers score coverage/DQS instead of being imputed.

The source implementation status is maintained in `config/SECTOR_ROTATION_V2_SOURCE_REGISTRY.csv`.

## Theme propagation and second-order opportunities

The functional target supports first-, second- and third-order economic beneficiaries, for example:

`AI -> compute -> semiconductors -> servers -> data centers -> cooling -> power -> grid -> generation -> software/application beneficiaries`.

V2.0 Shadow establishes the scoring/governance contract. Instrument-theme mappings, transmission strength and transmission lag require effective-dated mappings before they can influence RARS.

## Pipeline integration

V1 remains in the existing enrichment `WAVE_10_SECTOR_ROTATION`.

V2 runs after a successful current refresh through `sector_rotation_v2_shadow_run` and therefore consumes the current enriched Action master rather than a stale master.

Main outputs:

- `outputs/sector_rotation/V2_SECTOR_ROTATION_SHADOW.csv`
- `outputs/audit/V2_SECTOR_ROTATION_SHADOW.json`
- `state/sector_rotation_v2/SECTOR_ROTATION_V2_HISTORY.csv`

The unified summary explicitly marks V2 decision influence as zero.

## PIT/OOS requirements before promotion

Historical validation must reconstruct what was actually known at each historical `as_of` date. The following are prohibited:

- current ETF holdings projected into history;
- current consensus used for prior dates;
- revised macro series used as if they were originally known;
- future sector classifications/mappings projected backward without effective dates;
- thresholds selected on final holdout results.

For each historical signal, measure at minimum forward outcomes at J+5, J+20, J+60, J+120 and J+250, including excess return, MAE, MFE and drawdown.

The warning study must explicitly compare AVCR/no-chase thresholds and measure both:

- losses/drawdown avoided; and
- upside missed because of excessive prudence.

## Promotion criteria

V2 cannot influence Actions/ETF until all conditions below are met:

- positive OOS expected value;
- improvement versus V1/baseline on multiple periods;
- no material deterioration in max drawdown;
- stable results across several sector families;
- acceptable false-rotation/warning rates;
- controlled turnover;
- zero detected look-ahead;
- adequate DQS and PIT coverage;
- no performance attribution borrowed from another model/version.

Promotion must be explicit and versioned. The V2.0 Shadow configuration itself must never silently become active.

## Design principle

The target decision is not: **which sector has risen the most?**

It is: **which sector is improving, is beginning to attract confirmation, still offers a defensible valuation, and has not already discounted an unrealistically perfect future?**
