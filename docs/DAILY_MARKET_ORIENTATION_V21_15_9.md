# Daily market orientation V21.15.9

## Scope

Lightweight upstream market context added to the official Daily runtime. It is context-only and has no authority over model scores, decisions, weights, thresholds, candidate creation/blocking, T1/T2 or real orders.

## Daily indicators

- `VIXCLS` — FRED/CBOE, daily close, with previous observation and daily change.
- `CNN_FEAR_GREED` — CNN Fear & Greed public graph data, including score, rating, previous close, one-week and one-month references when available.
- `VSTOXX` — official STOXX `V2TX` historical data file. The hardcoded official URL is parsed only for `V2TX`; the observation is rejected if older than 10 calendar days. The runtime first uses normal TLS verification and, only if the STOXX certificate chain fails in GitHub Actions, retries the same hardcoded official URL with TLS verification disabled. That transport condition is explicitly audited as `transport_tls_verification=false`.

## Freshness and fallback

- VIXCLS cache maximum age: 10 calendar days.
- CNN Fear & Greed cache maximum age: 2 calendar days.
- VSTOXX cache maximum age: 10 calendar days.
- A stale cached value is never reused. Missing/failed sources remain explicit and non-blocking.
- The three source calls execute concurrently.

## Outputs

The context is exposed in:

- `outputs/market_orientation/DAILY_MARKET_ORIENTATION_V21_15_9.json`
- `outputs/market_orientation/DAILY_MARKET_ORIENTATION_V21_15_9.csv`
- `outputs/audit/DAILY_MARKET_ORIENTATION_V21_15_9.json`
- `outputs/committee_master/CI_MARKET_ORIENTATION.csv`
- CI Word report `CI_COMITE_INVESTISSEMENT.docx`
- CI Excel workbook sheet `MARKET_ORIENTATION`
- Android CI control center.

The consolidated Daily audit also embeds the complete market-orientation payload.

## Validation

Official Daily workflow run `32732046590`, head `1fec7f53e8cd9863f9e621639da07e7e47fcabc9`: SUCCESS on all workflow steps.

Observed values in the validation run:

- VIXCLS: 16.01 vs 14.89, +7.52%, as-of 2026-08-20, `RISK_OFF`.
- CNN Fear & Greed: 55.17, `GREED`, as-of 2026-08-24T13:03:16Z, direction `NEUTRAL` vs previous close.
- VSTOXX: 15.8992 vs 16.7943, -5.33%, as-of 2026-08-21, `RISK_ON`, official STOXX V2TX source.
- Synthetic lightweight orientation: `MIXED_NEUTRAL`.

Measured runtime:

- market orientation upstream: 0.440 s
- CI publication of market context: 0.751 s
- total Daily with market orientation: 78.433 s

Validated invariants:

- 3,760 exhaustive Daily decisions retained: 1,829 Action CT + 1,829 Action TCT + 102 ETF CT.
- CI detail selection: 91 BUY_CANDIDATE/WATCH rows.
- reference completeness: true.
- score reconstruction within 0.02 point: true; maximum absolute delta 0.000049 point.
- decision influence from market orientation: false.
- score influence from market orientation: 0.0.
- criteria/weights/thresholds unchanged.
- T1/T2 scope unchanged: Action TCT only.
- real orders disabled.
