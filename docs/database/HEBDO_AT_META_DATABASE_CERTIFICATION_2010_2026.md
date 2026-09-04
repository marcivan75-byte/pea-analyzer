# HEBDO AT META — Database certification 2010–2026

Certification snapshot: 2026-09-04

## Scope

- Combined OHLCV rows: 5,635,905
- Combined tickers: 1,782
- Window: 2010-01-04 through 2026-08-27
- DEVELOPMENT: 2010–2022, 4,055,044 rows, 1,623 tickers
- HOLDOUT: 2023–2026, 1,580,861 rows, 1,782 tickers
- Duplicate ticker/date rows: 0
- Volume non-null: 100%
- Holdout fitting: blocked
- Synthetic repair: forbidden

## OHLC certification

Strict/tolerant geometry audit result:

- Certified OHLC rows: 5,635,174 / 5,635,905 = 99.987030%
- Quarantined OHLC rows: 731
- Floating-point rounding geometry artifacts accepted by tolerance: 1,412
- DEVELOPMENT 2010–2022: 100% OHLC certified after the governed PRE2023 fail-closed filtering.
- HOLDOUT anomalies are quarantined; source rows are not synthetically repaired.

Yearly certified OHLC percentages:

| Year | Rows | Tickers | Certified % | Quarantine-equivalent invalid rows |
|---|---:|---:|---:|---:|
| 2010 | 250,932 | 994 | 100.000000 | 0 |
| 2011 | 257,384 | 1,021 | 100.000000 | 0 |
| 2012 | 261,102 | 1,043 | 100.000000 | 0 |
| 2013 | 266,379 | 1,071 | 100.000000 | 0 |
| 2014 | 277,641 | 1,122 | 100.000000 | 0 |
| 2015 | 292,708 | 1,183 | 100.000000 | 0 |
| 2016 | 308,217 | 1,223 | 100.000000 | 0 |
| 2017 | 316,909 | 1,276 | 100.000000 | 0 |
| 2018 | 329,389 | 1,330 | 100.000000 | 0 |
| 2019 | 343,045 | 1,384 | 100.000000 | 0 |
| 2020 | 358,657 | 1,443 | 100.000000 | 0 |
| 2021 | 384,940 | 1,563 | 100.000000 | 0 |
| 2022 | 407,741 | 1,623 | 100.000000 | 0 |
| 2023 | 419,678 | 1,680 | 99.908501 | 384 certified-row failures |
| 2024 | 430,431 | 1,716 | 99.930535 | 299 certified-row failures |
| 2025 | 438,584 | 1,758 | 99.997720 | 10 certified-row failures |
| 2026 YTD | 292,168 | 1,782 | 99.986994 | 38 certified-row failures |

Note: invalid_price_rows and invalid_geometry_rows can overlap; the authoritative quarantine total is 731 unique rows.

## PRE2023 source certification

Yahoo raw bootstrap, auto_adjust=false, repair=false, fail-closed row exclusion:

- Identity tickers requested: 1,782
- OK tickers: 1,623
- NO_HISTORY under the current ticker: 158
- ALL_ROWS_EXCLUDED: 1 (`NOFIN.OL`)
- Raw rows: 4,067,835
- Kept rows: 4,055,044
- Excluded rows: 12,791 (0.314442%)
- Duplicate ticker/date after filtering: 0
- Invalid OHLC after filtering: 0

### Qualification of the 158 NO_HISTORY tickers

Cross-check against the 2010–2026 combined database shows that all 158 have their first observation on or after 2023:

- first observed in 2023: 56
- first observed in 2024: 37
- first observed in 2025: 41
- first observed in 2026: 24
- first observed before 2023: 0

Therefore these records are reclassified for governance as:

`NO_PRE2023_HISTORY_FIRST_OBSERVED_POST2022`

This removes them from the manifest-level PRE2023 data-gap count under the current ticker. It does **not** by itself prove IPO status: ticker changes / identity succession still require historical-identity certification.

### NOFIN.OL

`NOFIN.OL` is `ALL_ROWS_EXCLUDED` in PRE2023 (2,798 raw rows, 0 kept) and also presents non-positive adjusted OHLC values in the HOLDOUT. Until the corporate-action / identity chain is resolved, it is:

`EXCLUDE_PRICE_FACTORS_IDENTITY_CORPORATE_ACTION_UNRESOLVED`

No synthetic repair is permitted.

## Volume certification

All 5,635,905 rows have a non-null numeric non-negative raw market volume. Zero volume is audited as a factor-usability issue, not as an automatic price-row deletion.

- Zero-volume rows: 700,297 (12.425635%)
- Zero-volume rows with absolute price move >1%: 30,827
- Zero-volume rows with absolute price move >5%: 8,930

Ticker-level volume-factor status:

| Status | Tickers | Policy |
|---|---:|---|
| CERTIFIED_VOLUME_COVERAGE | 1,139 | zero volume <5% |
| GOOD_VOLUME_COVERAGE | 267 | 5–20% or isolated suspicious zero-volume price moves |
| USABLE_WITH_LIMITATION | 191 | 20–50% |
| LOW_CONFIDENCE_VOLUME | 139 | 50–90% |
| EXCLUDE_VOLUME_FACTORS | 46 | >=90% |

Thus 1,406 / 1,782 tickers (78.90%) have certified or good volume coverage. The 46 excluded tickers remain eligible for price-only analyses if their OHLC status is valid.

## Corporate actions / adjusted prices

The PRE2023 corporate-action audit identified material adjustment-factor changes and raw-price jumps. Analytical price factors must therefore use the governed adjusted-price basis (`YAHOO_ADJ_CLOSE_RATIO_OHLC`), while raw OHLC is retained for audit. Raw OHLC must not be consumed naively across corporate actions.

## Remaining certification limitations

The price/volume database is technically usable under the gates above, but the historical *universe* is not yet production-certified:

- historical_universe_certified = false
- survivorship_safe = false
- historical_pea_eligibility_certified = false

The governed current-universe bootstrap cannot by itself reconstruct companies that were delisted/absorbed before the current reference universe was built. The attempted EODHD active+delisted inventory is currently blocked by the absence of `EODHD_API_TOKEN` in GitHub Actions, not by a code/data-quality failure.

## Allowed use

### CERTIFIED for factor research

- 2010–2022 adjusted OHLC rows after fail-closed filtering
- 2023–2026 rows with `certified_ohlc_row=true`
- Volume factors only according to ticker-level volume-factor status
- Strict PIT / anti-look-ahead and holdout isolation

### USABLE WITH LIMITATION

- Research frequencies and patterns that depend on the current governed identity universe, because survivorship-safe historical membership is not yet certified.
- Historical PEA-only claims until historical PEA eligibility is reconstructed.

### EXCLUDED

- 731 quarantined HOLDOUT OHLC rows
- `NOFIN.OL` price factors pending identity/corporate-action resolution
- Volume-derived factors for the 46 `EXCLUDE_VOLUME_FACTORS` tickers
- Any non-PIT consensus/news/fundamental value used retroactively.

## Certification decision

**TECHNICAL MARKET DATA (OHLCV): CERTIFIED WITH EXPLICIT QUARANTINES AND PER-TICKER VOLUME GATES.**

**HISTORICAL UNIVERSE / SURVIVORSHIP / HISTORICAL PEA MEMBERSHIP: LIMITED — NOT YET CERTIFIED.**

This distinction must be preserved in every reverse-engineering/backtest report.