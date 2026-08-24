# CI V22.2.2 — final selection gates

V22.2.2 extends V22.2.1 after scoring, confidence and market-entry context. It does not alter the underlying scoring formulas or weights.

## Effective selection rules

An instrument is retained only when all applicable gates pass:

- selection score >= 77;
- market-adjusted CI confidence score >= 66;
- Actions only: analyst-consensus upside >= 20%;
- ETFs are explicitly exempt from the analyst-consensus-upside gate.

Thresholds are inclusive. Missing Action analyst-consensus upside is fail-closed. A technical potential to the 52-week high can never substitute for analyst consensus.

## Source links

Every output row includes:

- `CI_BOURSORAMA_URL` and its resolution status;
- `CI_INVESTING_URL` and its resolution status.

Boursorama deterministic instrument URLs are preferred. Investing uses the validated exact-ISIN URL map when available. If a direct validated URL is unavailable, a site search URL is emitted and labelled `SEARCH_FALLBACK`; no unvalidated direct URL is presented as exact.

## Market orientation

The V22.2.1 lightweight orientation remains unchanged and independent of WAVE09:

- FRED: `VIXCLS` only;
- CNN Fear & Greed Index;
- VSTOXX (`V2TX`).

## Governance

- WAVE09 remains disabled.
- Base selection scores are not overwritten.
- Base criteria and weights are unchanged.
- V22.2.2 deliberately changes the effective final shortlist through the explicit user-requested gates above.
- T1/T2 remain ACTION TCT only.
- Real orders remain disabled.
