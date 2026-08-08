# SMART MONEY V1 — RC1 integration audit

## Verdict
**GO FOR NEXT GITHUB SHADOW RUN / NO-GO FOR ACTIVE SCORING.**

The Smart Money module is now executable end-to-end on `feature/smart-money-v1` and remains isolated from `main` and from the V18.2 scoring namespace.

## Corrections and completion delivered in RC1
1. **Structural calibration** — WIS and IFS contribution budgets are conservatively capped (max shadow delta: 5.0 points Actions, 3.5 points ETF). Calibration is explicitly labelled structural, not empirical alpha fitting.
2. **Short data history** — official AMF short ingestion now preserves prior public observations per holder so that increases and covering can be measured instead of defaulting to a zero delta.
3. **Censoring below 0.5%** — last published sub-0.5 observation remains censored and is never converted to zero.
4. **Threshold-event decay** — old shareholder-threshold crossings decay with publication age and cannot accumulate indefinitely in WIS.
5. **Cross-source deduplication** — AMF/Finnhub events are grouped by an economic-event key independent of source-document identity; higher evidence wins and equal-evidence conflicts quarantine.
6. **Tape event controls** — earnings, index-rebalance and corporate-action multipliers are applied to reduce or neutralize mechanically distorted volume signals.
7. **ETF flow bootstrap** — AUM changes are performance-adjusted by NAV. Scoring remains neutral until enough AUM+NAV history exists and stale history is rejected.
8. **ETF coverage registry** — every ETF receives a provider-specific or generic normalized ingestion route. Registry coverage is distinct from actual 20-day flow readiness; no missing history is fabricated.
9. **ETF live bootstrap fallback** — yfinance metadata may add a daily AUM+NAV snapshot only when both `totalAssets` and `navPrice` are present; market price is never substituted for NAV.
10. **Official AMF bridge** — normalized AMF director/threshold imports remain evidence A and fail closed. Finnhub insider transactions remain evidence B fallback where validated symbol coverage exists.
11. **Field-level provenance** — heterogeneous Smart Money derived fields remain in the provenance sidecar rather than rewriting the V18.2 provenance model.
12. **Executable orchestrator** — `v183.reporting.run` executes V18.2 first, requires its quality gates, runs Smart Money Wave 09, persists events/scores/provenance/coverage/calibration and blocks artifacts when RC1 gates fail.
13. **Shadow hard lock** — changing only `shadow_mode` cannot activate Smart Money. Active scoring additionally requires explicit calibration approval and removal of the empirical walk-forward blocker.

## Coverage semantics
- **Provider registry coverage**: integration route available for every ETF in the V18.2 PEA ETF master.
- **Flow-ready coverage**: ETF has enough persisted AUM+NAV observations to calculate a 20-observation performance-adjusted flow signal.
- The first RC run is expected to bootstrap flow history. A low initial flow-ready percentage is therefore a measured state, not converted into synthetic data.
- AMF short open data is a full official source feed when available. AMF director/threshold scoring remains limited to validated normalized official imports until a stable machine-readable document extraction path is validated.

## Remaining blocker before active scoring
The only intentional release blocker is **empirical calibration**: active WIS/IFS must remain disabled until enough point-in-time shadow observations and distinct run dates exist for walk-forward validation. RC1 requires at least 20 flow observations for an ETF signal and the calibration contract requires at least 20 distinct shadow run dates before an empirical fit can be considered.

## Release recommendation
Run **V18.3 RC1 in shadow mode on `feature/smart-money-v1`**. Inspect the generated coverage, event quarantine, score-delta distribution and quality evidence. Only after the next run is green should a controlled PR be prepared; never direct-push to `main`.
