# IPO Radar V1.1 — Operational process

## Objective

Detect future IPOs as early as possible, evaluate opportunity and risk with auditable evidence, surface material changes to the investment committee, and build a forward performance dataset for later PIT/OOS validation.

## Discovery sources

1. SEC EDGAR full index: S-1 and F-1 initial registration filings, 45-day rolling discovery window.
2. Nasdaq IPO calendar: upcoming and filed candidates.
3. Finnhub IPO calendar: expected/priced/filed/withdrawn candidates when the API key is available.
4. Euronext IPO showcase: European listing identity metadata and market/location information when a future row is published.

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

When SEC Company Facts is available, the module also derives conservative scores from revenue growth, gross margin, operating leverage, cash, assets, liabilities and operating cash flow.

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
- All forward validation uses observations persisted before the measured post-listing outcome.
