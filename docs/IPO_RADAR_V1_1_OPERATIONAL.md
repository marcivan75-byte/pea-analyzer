# IPO Radar V1.1 — Operational process

## Objective

Detect future IPOs as early as possible, evaluate opportunity and risk with auditable evidence, surface material changes to the investment committee, convert missing information into explicit due-diligence actions, and build a forward performance dataset for later PIT/OOS validation.

## Discovery sources

The runtime uses redundant calendars rather than a single dependency:

1. Euronext IPO showcase for European listing identity, market and location data.
2. Nasdaq IPO calendar for upcoming and filed US candidates.
3. Alpha Vantage IPO calendar through the API key already available in the project.
4. Finnhub IPO calendar for expected/priced/filed/withdrawn candidates.
5. SEC EDGAR / EFTS for early S-1 and F-1 discovery and prospectus due diligence when the execution network can reach SEC endpoints.

SEC access is best-effort because hosted GitHub runners may receive HTTP 403 responses from SEC endpoints. SEC failure therefore degrades the module but does not suppress Nasdaq, Alpha Vantage, Finnhub or Euronext discovery.

All candidates are deduplicated by normalized issuer identity. SEC CIK is the preferred stable identity once resolved.

## SEC due diligence

For SEC-matched candidates the module retrieves the latest available S-1, S-1/A, F-1, F-1/A, 424B3 or 424B4 prospectus and parses deterministic evidence for:

- going-concern language;
- material weaknesses in internal controls;
- dual-class/super-voting governance;
- customer concentration;
- regulatory investigations/material litigation;
- secondary selling shareholders;
- lock-up duration;
- recognized underwriters;
- use-of-proceeds quality.

When SEC Company Facts is available, the module also derives conservative US-GAAP or IFRS scores from revenue growth, gross margin, operating leverage, cash, assets, liabilities and operating cash flow.

No LLM-generated factual assertion is used in the scoring path. Missing evidence remains missing and is handled through active-weight renormalization plus minimum coverage gates.

## Scoring

Opportunity score: 12 weighted criteria totaling 100%.

Risk score: 12 weighted criteria totaling 100%.

Net IPO score = 60% opportunity + 40% inverse risk.

A candidate cannot reach PRIORITY_DD unless:

- net score >= 75;
- opportunity >= 75;
- risk <= 35;
- minimum opportunity/risk coverage >= 75%;
- market readiness >= 60.

A candidate cannot reach DEEP_DD unless:

- net score >= 65;
- opportunity >= 65;
- risk <= 50;
- minimum opportunity/risk coverage >= 65%;
- market readiness >= 50.

Early SEC filings without a sufficiently mature market setup are capped at WATCH_EARLY_FILING.

## Actionable due diligence gaps

`IPO_DD_GAPS.csv` converts every missing scored criterion into an operational worklist. For each IPO it publishes:

- number of missing criteria;
- total scoring weight still undocumented;
- the three highest-priority missing criteria;
- category of work required (valuation, financials, offer terms, governance, legal, business model, sector, accounting, balance sheet or prospectus);
- precise next action to execute;
- exhaustive list of remaining missing criteria and actions.

Prioritization starts with the criterion weight, then a deterministic work-category priority. A missing high-weight valuation or revenue-growth criterion is therefore treated before a low-weight secondary criterion. The worklist can never authorize an order.

## Hard blocks

The following flags override favorable scores:

- going concern;
- material auditor qualification;
- unresolved fraud/accounting restatement;
- sanctions/listing ineligibility;
- insufficient 12-month post-offering liquidity when established by reliable evidence.

## Alerts

The module emits IPO_ALERTS.csv for:

- new candidates;
- withdrawal;
- price-range midpoint revision >= 5%;
- listing delay >= 5 days;
- risk deterioration >= 10 points;
- decision upgrade/downgrade;
- new SEC prospectus accession;
- new hard-block flag.

HIGH and CRITICAL alerts are surfaced in IPO_COMMITTEE_BRIEF.json.

## Forward validation loop

After listing, IPO_OUTCOMES.csv records observable market outcomes when market data is available:

- first close;
- first close versus offer midpoint;
- D+5 return from first close;
- D+20 return from first close;
- D+60 return from first close.

The outcome is joined to the last available pre-listing decision. IPO_VALIDATION_STATUS.json aggregates positive rate, average return and median return by pre-listing decision.

Promotion remains disabled even after the initial sample target is reached. A dedicated PIT/OOS audit is mandatory before any IPO score can influence an automatic BUY.

## Outputs

- outputs/ipo_radar/IPO_RANKING.csv
- outputs/ipo_radar/IPO_SUMMARY.json
- outputs/ipo_radar/IPO_SOURCE_STATUS.csv
- outputs/ipo_radar/IPO_SEC_DD_STATUS.csv
- outputs/ipo_radar/IPO_ALERTS.csv
- outputs/ipo_radar/IPO_COMMITTEE_BRIEF.json
- outputs/ipo_radar/IPO_DD_GAPS.csv
- outputs/ipo_radar/IPO_VALIDATION_STATUS.json
- state/ipo_radar/IPO_HISTORY.csv
- state/ipo_radar/IPO_OUTCOMES.csv

## Governance

- Shadow/advisory only.
- No automatic BUY.
- No real order generation.
- T1/T2 forbidden.
- PEA eligibility never inferred solely from exchange or ISIN prefix.
- All missing-data coverage is explicit.
- All due-diligence actions are advisory and traceable.
- All forward validation uses observations persisted before the measured post-listing outcome.
