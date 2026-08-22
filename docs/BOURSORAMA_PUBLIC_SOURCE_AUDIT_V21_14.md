# V21.14.2 — Audit Boursorama public source hub

## Objective

Use Boursorama public instrument pages as a cache-first attributed aggregation source where this can reduce duplicated provider calls without degrading PEA Analyzer data, active criteria, scores, weights, thresholds, freshness, provenance, auditability or CI documents.

This version remains deliberately `SHADOW_ATTRIBUTED_NO_DECISION_INFLUENCE`. It measures real coverage and semantic equivalence before any existing provider is suppressed.

## Verified public data families

### Actions

Public quote/consensus/company pages expose, depending on instrument coverage:

- quote state, open, previous close, high/low, volume, market capitalization, sector, reference index and PEA eligibility;
- FactSet recommendation distribution: Acheter / Renforcer / Conserver / Alléger / Vendre;
- analyst count and historical recommendation snapshots;
- median consensus note, median target and displayed upside;
- forward forecasts including EPS, PER, dividend/yield, revenue, EBITDA, EBIT, net debt, book value per share and cash-flow-per-share on verified pages;
- annual company statements and ratios from Cofisem, including revenue, operating result, net income, balance-sheet financial debt, operating margin and ROE;
- company-specific news/events on instrument pages.

### ETFs

Verified public tracker pages expose, depending on instrument coverage:

- quote and identification data, PEA eligibility, AUM and management company;
- asset class, geographic zone, distribution policy, replication and management fee;
- Morningstar category and performance/rank information;
- risk measures such as volatility, alpha, R-squared and beta;
- holdings and geographic/sector allocations.

## Source replacement matrix

| Current collection | Boursorama coverage | V21.14 status | Rationale |
|---|---|---|---|
| Yahoo OHLCV bulk | No replacement | KEEP | Yahoo/local parquet remains more efficient for historical batch data and technical calculations. |
| Finnhub recommendation consensus | Strong | SHADOW -> replacement candidate | Five Boursorama/FactSet buckets map exactly to the existing 5/4/3/2/1 weighting formula. |
| Finnhub price target | Partial, different aggregation | KEEP/FALLBACK | Boursorama publishes a median target while current Finnhub/Yahoo fields may represent a mean. |
| Yahoo Action `get_info` WAVE04 | Strong but incomplete | KEEP during shadow | Verified Boursorama structured pages still do not directly provide exact current `free_cash_flow` or Action `beta` semantics. |
| Yahoo ETF info WAVE06 | Strong | replacement candidate after ETF parser validation | Boursorama exposes AUM, category, management/structure and additional ETF information. |
| Morningstar ETF-derived public data | Strong via Boursorama pages | replacement/augmentation candidate | Risk/category/rank/composition information is presented on public tracker pages. |
| Morningstar Action rating authorized snapshot | Not equivalent | KEEP | Never infer a stock star rating from unrelated Boursorama fields. |
| GDELT global/country/sector news | Not equivalent | KEEP | Instrument pages do not replace global macro/country/sector news coverage. |
| GDELT Action instrument news | Rich headlines/events but scoring mismatch | SHADOW only | Current GDELT lexical formula is not semantically interchangeable with French Boursorama headlines. |

## Consensus semantic equivalence

The current Finnhub collector uses strongBuy/buy/hold/sell/strongSell weights 5/4/3/2/1.

V21.14 maps Boursorama/FactSet buckets without changing that formula:

- Acheter = 5
- Renforcer = 4
- Conserver = 3
- Alléger = 2
- Vendre = 1

Shadow fields include current score, analyst count, buy/hold/sell counts, 4-week score delta, net-upgrades proxy and broker-weighted revision. The Boursorama median target remains provider-specific and is **not** written to the current mean-target canonical field.

## Resolver policy

No bulk use of Boursorama site search is allowed. Only deterministic public identifiers or validated static overrides are eligible.

Verified deterministic Action patterns now included in V21.14.2:

- Euronext Paris: Yahoo `AI.PA` -> Boursorama `1rPAI`
- Euronext Amsterdam: Yahoo `ASML.AS` -> Boursorama `1rAASML`
- Euronext Lisbon: Yahoo `EDP.LS` -> Boursorama `1rLEDP`
- Euronext Brussels: Yahoo `ABI.BR` -> Boursorama `FF11-ABI`
- Bolsa Madrid: Yahoo `SAN.MC` -> Boursorama `FF55-SAN`
- Borsa Italiana: Yahoo `ENI.MI` -> Boursorama `1gENI`
- Xetra: Yahoo `SIE.DE` -> Boursorama `1zSIE`

The verified Paris ETF pattern remains Yahoo `WPEA.PA` -> Boursorama `1rTWPEA`.

Any other suffix remains unsupported unless a validated static `boursorama_code` override exists. The resolver returns `None` rather than inventing an identifier, and current providers remain the fallback.

## Load and storage policy

The hub minimizes provider load and GitHub runtime:

1. cache first;
2. bounded weekly live refresh budget of 120 Action consensus pages during shadow validation;
3. no ordinary daily bootstrap; daily tactical mode is cache-only;
4. global minimum interval of 1 second between Boursorama request starts;
5. up to 4 requests may be in flight so response latency does not serialize the collector, without increasing request-start cadence;
6. no protected/private/AJAX endpoint use;
7. no browser automation when public HTML is sufficient;
8. discard raw HTML immediately after parsing;
9. persist normalized scalar values, source URL, UTC fetch timestamp and page SHA-256 only;
10. unsupported/failed instruments retain current provider fallback.

## Normal-run integration

The shadow Action collector runs inside the existing enrichment runner as a peer of Yahoo WAVE04:

- Yahoo WAVE04 and Boursorama shadow receive separate copies of the canonical Action frame;
- providers, rate limiters, caches and audit files are independent;
- the Boursorama branch never applies observations to the Action master and has `decision_influence = 0`;
- any Boursorama exception is `FAILED_SHADOW_NON_BLOCKING`; the existing Yahoo/Finnhub chain continues unchanged;
- the 120-page budget, four in-flight requests and one request start per second are intended to keep shadow work under the historically much longer WAVE04 wall time;
- no extra heavy benchmark workflow is created.

Dedicated audit outputs:

- `outputs/audit/BOURSORAMA_PUBLIC_SHADOW_OBSERVATIONS.csv`
- `outputs/audit/BOURSORAMA_PUBLIC_SHADOW_FAILURES.csv`
- `outputs/audit/BOURSORAMA_PUBLIC_SHADOW_METRICS.json`
- `outputs/audit/BOURSORAMA_PUBLIC_EQUIVALENCE.json`
- `state/provenance/source_cache/BOURSORAMA_PUBLIC_V1.json`

## Activation gates before any provider suppression

A replacement switch may be enabled only after a normal representative run demonstrates:

- >=95% parser success on actually requested supported pages;
- measured deterministic-code coverage by exchange/universe;
- no regression in availability of any active weighted criterion;
- consensus formula equivalence checks against current Finnhub fields;
- explicit handling of target median-vs-mean difference;
- no material provider errors/throttling;
- actual wall/network telemetry showing a net runtime benefit;
- unchanged score weights, thresholds, universes and execution gate.

No additional heavy full workflow is required solely for benchmarking. The next normal scheduled/full run should provide the telemetry.

## Remaining optimization path

The safe suppression sequence remains:

1. Boursorama consensus -> skip Finnhub recommendation calls only where a fresh validated Boursorama snapshot exists; retain target fallback independently.
2. ETF Boursorama parser -> suppress redundant Yahoo/Morningstar ETF metadata calls only where exact semantics and freshness are covered.
3. Reduce WAVE04 only after remaining active Action gaps (`free_cash_flow`, Action beta or a formally accepted replacement methodology) are closed without coverage loss.
4. Consider instrument-news substitution only after multilingual sentiment equivalence/backtesting, while keeping GDELT global/country/sector layers.

This order favors measurable runtime reduction without hidden data degradation.
