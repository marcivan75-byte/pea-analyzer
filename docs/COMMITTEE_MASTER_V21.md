# Committee Master V21 — full decision wiring

## Scope
This wiring connects the complete PEA Analyzer decision chain without replacing the exhaustive referentials by optimized submodels.

### Actions PEA
- CT, MT, LT, Short-risk and Top-Down are scored from the V21.0 registry.
- The registry references 633 criteria/fields and the engine preserves all input fields.
- Non-zero baseline weights score; zero-weight fields remain preserved for gates, controls and future reweighting.
- TCT is a separate SHADOW adapter. T1/T2 are ACTION TCT only and have zero influence on the base score.

### ETF PEA
- CT: V20.7.1 active provisional weighting (~69% positive OOS in the referenced backtest).
- MT: V20.8.1 dynamic 38-criterion engine. The historical 90.91% validation applies only to that dynamic PIT core.
- LT, Short-risk and Top-Down: V20.7 baseline weights.
- The full 268-field ETF referential remains the committee reference, including Morningstar, dividend yield, TER, AUM, diversification, replication/tracking, risk, liquidity and other qualitative/structural fields. A zero weight does not delete a field or make it irrelevant for future reweighting.

### Gold
The workflow is connected but deliberately blocks the Gold module until the exact GOLD V1 102-criterion PIT registry is present as `config/GOLD_V1_102_CRITERIA.json`. No missing Gold criterion or weight is fabricated.

## Outputs
`outputs/committee_master/`
- `COMMITTEE_DECISIONS.csv`: one line per instrument/horizon or blocking module state.
- `SECTOR_RANKING.csv`: ranking inside each sector, asset class and horizon.
- `SUMMARY.json`: data-source status, registry integrity, module status and decision counts.

## Data-quality behavior
Missing weighted criteria reduce weighted coverage. Below the horizon coverage threshold the instrument is `BLOCK_DATA`; the engine does not silently declare a fully valid score from a partial subset.

## Execution
This is reference/shadow scoring only. Real orders are disabled.

## Workflow
`.github/workflows/committee_master_daily.yml`:
1. refresh Actions/ETF data through V18.2 enrichment;
2. run ETF MT V20.8.1;
3. run the Committee Master consolidator even if an upstream optional module fails;
4. publish sector-ranked artifacts and explicit blocked states.

The workflow has `workflow_dispatch` and a weekday schedule.
