# V21.14.1 — Audit Boursorama public source hub

## Objective

Use Boursorama public instrument pages as a cache-first attributed aggregation source where this can reduce duplicated provider calls without degrading PEA Analyzer data, active criteria, scores, weights, thresholds, freshness, provenance, auditability or CI documents.

This version remains deliberately `SHADOW_ATTRIBUTED_NO_DECISION_INFLUENCE`. It measures real coverage and semantic equivalence before any existing provider is suppressed.

## Verified public data families

### Actions

Public quote/consensus/company pages expose, depending on instrument coverage:

- quote state, open, previous close, high/low, volume, market capitalization, sector, reference index and PEA eligibility;
- FactSet recommendation distribution: Acheter / Renforcer / Conserver / Alléger / Vendre;
- analyst count and historical recommendation snapshots (3m / 2m / 1m / 7d / current on verified pages);
- median consensus note, median target and displayed upside;
- forward forecasts including EPS, PER, dividend/yield, revenue, EBITDA, EBIT, net debt, book value per share and cash-flow-per-share on verified consensus pages;
- annual company statements and ratios from Cofisem, including revenue, operating result, net income, balance-sheet financial debt, operating margin and ROE;
- company-specific news/events on instrument news pages.

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
| Finnhub recommendation consensus | Strong | SHADOW -> replacement candidate | Five Boursorama/FactSet buckets map exactly to the existing 5/4/3/2/1 weighting formula. Historical 1-month buckets can reproduce the existing consensus-delta and net-upgrade logic. |
| Finnhub price target | Partial, different aggregation | KEEP/FALLBACK | Verified Boursorama page publishes a median target while current Finnhub/Yahoo fields may represent a mean. Do not silently substitute median for mean. |
| Yahoo Action `get_info` WAVE04 | Strong but incomplete | KEEP during shadow | Boursorama covers many active fundamentals, but verified structured pages do not directly provide the current `free_cash_flow` or Action `beta` semantics. Removing Yahoo now could reduce weighted MT coverage. |
| Yahoo ETF info WAVE06 | Strong | replacement candidate after ETF parser validation | Boursorama exposes AUM, category, management/structure and additional ETF information. Runtime saving is secondary because WAVE06 is already much smaller than WAVE04/WAVE09. |
| Morningstar ETF-derived public data | Strong via Boursorama pages | replacement/augmentation candidate | Risk/category/rank/composition information is already presented on Boursorama tracker pages. Attribution must remain explicit. |
| Morningstar Action rating authorized snapshot | Not equivalent | KEEP | Do not infer a Morningstar stock star rating from unrelated Boursorama fields. |
| GDELT global/country/sector news | Not equivalent | KEEP | Boursorama instrument pages do not replace global macro/country/sector news coverage. |
| GDELT Action instrument news | Rich headlines/events but scoring mismatch | SHADOW only | Current GDELT lexical formula uses an English lexicon. Boursorama headlines are often French; replacing the source without a validated multilingual scoring layer would change criterion semantics. |

## Consensus semantic equivalence

The current Finnhub collector uses the following weights:

- strongBuy = 5
- buy = 4
- hold = 3
- sell = 2
- strongSell = 1

V21.14 maps Boursorama/FactSet buckets without changing that formula:

- Acheter = 5
- Renforcer = 4
- Conserver = 3
- Alléger = 2
- Vendre = 1

It derives shadow fields for current score, analyst count, buy/hold/sell counts, 4-week score delta, net-upgrades proxy and broker-weighted 30-day revision. The Boursorama median target remains provider-specific and is **not** written to the current mean-target canonical field.

## Resolver policy

No bulk use of Boursorama site search is allowed. Only deterministic public identifiers or validated static overrides are eligible.

Initial deterministic patterns:

- Euronext Paris Action: Yahoo `AI.PA` -> Boursorama `1rPAI`
- Euronext Amsterdam Action: Yahoo `ASML.AS` -> Boursorama `1rAASML`
- Euronext Lisbon Action: Yahoo `EDP.LS` -> Boursorama `1rLEDP`
- Euronext Paris ETF: Yahoo `WPEA.PA` -> Boursorama `1rTWPEA`

Markets where Boursorama identifiers are not deterministic from the Yahoo suffix (examples observed for Madrid/Brussels) remain unsupported until a validated static mapping exists. They fall back to current providers.

## Load and storage policy

The hub is designed to minimize both provider load and GitHub runtime:

1. cache first;
2. bounded weekly live refresh budget of 120 Action consensus pages during shadow validation;
3. no ordinary daily bootstrap; daily tactical mode is cache-only;
4. global minimum interval of 1 second between Boursorama request starts;
5. up to 4 requests may be in flight so network latency does not serialize the collector, without increasing the request-start cadence;
6. no protected/private/AJAX endpoint use;
7. no browser automation when public HTML is sufficient;
8. discard raw HTML immediately after parsing;
9. persist normalized scalar values, source URL, UTC fetch timestamp and page SHA-256 only;
10. unsupported/failed instruments retain current provider fallback.

## Normal-run integration V21.14.1

The shadow Action collector is launched inside the existing enrichment runner as a peer of Yahoo WAVE04:

- Yahoo WAVE04 and Boursorama shadow receive separate immutable copies of the canonical Action frame;
- their network providers, rate limiters, caches and audit files are independent;
- the Boursorama branch never calls `apply_observations`, never modifies the Action master and has `decision_influence = 0`;
- any Boursorama exception is written as `FAILED_SHADOW_NON_BLOCKING` and the existing Yahoo/Finnhub chain continues unchanged;
- with the 120-page budget, four in-flight requests and one request start per second, the shadow work is intended to remain hidden under the historically much longer WAVE04 wall time;
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
- no new material provider errors/throttling;
- actual wall/network telemetry showing a net runtime benefit;
- unchanged score weights, thresholds, universes and execution gate.

No additional heavy full workflow is required solely for benchmarking. The next normal scheduled/full run should provide the telemetry.

## Remaining optimization path

The most promising safe suppression sequence is:

1. Boursorama consensus -> skip Finnhub recommendation call where a fresh validated Boursorama snapshot exists; retain target fallback independently.
2. ETF Boursorama parser -> suppress redundant Yahoo/Morningstar ETF metadata calls where exact semantics and freshness are covered.
3. WAVE04 reduction only after the remaining active Action gaps (`free_cash_flow`, Action beta or a formally accepted replacement methodology) are closed without coverage loss.
4. Instrument-news substitution only after a multilingual sentiment equivalence/backtest, while keeping GDELT global/country/sector layers.

This order favors measurable runtime reduction without creating hidden data degradation.
