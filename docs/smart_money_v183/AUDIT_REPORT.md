# SMART MONEY V1 — Pre-integration audit

## Verdict
**GO FOR PRE-INTEGRATION / NO-GO FOR DIRECT PRODUCTION ACTIVATION.**

The concept is sound, but the first specification required hardening before code integration.

## Critical corrections applied
1. **Short data source** — use the official daily AMF/data.gouv CSV; do not scrape BDIF for shorts.
2. **Censoring below 0.5%** — preserve last published sub-0.5 observation as censored; never convert it to zero.
3. **No-look-ahead** — scoring/backtests use publication availability date, never the earlier transaction date.
4. **AMF insider/threshold automation** — fail closed until a stable machine endpoint is validated. Official normalized imports are A; Finnhub is B fallback.
5. **Field-level provenance** — V18.2 row-level evidence metadata is insufficient for heterogeneous Smart Money fields. Sidecar provenance added.
6. **Shadow mode** — mandatory before WIS/IFS modifies decisions.
7. **Event store** — regulatory events remain event-level and only aggregates enter masters.
8. **Double counting** — deduplication by economic event ID; higher evidence wins; equal-evidence conflicts quarantine.
9. **Confidence** — score is multiplied by evidence/completeness confidence and capped when confidence <60%.
10. **ETF flows** — AUM is performance-adjusted with NAV; raw AUM change is forbidden as a flow signal.

## Current residual risks before GitHub integration
- Live AMF BDIF director/threshold document parsing remains intentionally unimplemented until a stable endpoint/format is validated.
- ETF AUM/NAV/shares-outstanding issuer adapters still need issuer-by-issuer coverage mapping.
- Coefficients are defaults, not optimized parameters; shadow backtests must calibrate them.
- Existing V18.2 provenance model should not be globally rewritten in the same PR as Smart Money; use sidecar first to minimize regression risk.

## Release recommendation
Integrate as **V18.3 release candidate in shadow mode**, not as a direct scoring change.
