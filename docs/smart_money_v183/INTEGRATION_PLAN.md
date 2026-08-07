# V18.3 SMART MONEY — Integration plan

## Non-negotiable release path
1. Keep V18.2/main unchanged.
2. Create `feature/smart-money-v1` only after this pre-integration bundle passes review.
3. Copy module into a V18.3 namespace (or intentionally refactor shared modules); do not overwrite v182.
4. Add Wave 09A AMF shorts + official-event bridge, 09B tape, 09C ETF flows, 09D aggregation.
5. Run in `shadow_mode=true`; `score_final` remains V18.2 score and `score_shadow` receives WIS/IFS.
6. Persist `SMART_MONEY_EVENTS.parquet`, `SMART_MONEY_DAILY_SCORES.parquet`, and field-level provenance.
7. Add Smart Money quality gates to the workflow before artifact publication.
8. Backtest using publication dates only; no transaction-date look-ahead.
9. Four audits: unit/data-quality, integration/non-regression, backtest/no-lookahead, CI/security.
10. Only then open a controlled PR; never direct-push to main.

## V18.2 integration hooks
- Reuse existing OHLCV cache for Tape Intelligence.
- Reuse FINNHUB_API_KEY already present in GitHub Actions.
- Do not add an API secret for AMF short data.
- Keep Finnhub ownership disabled unless the current plan proves access.
- Extend source registry with AMF Open Data Shorts (A), AMF BDIF Official Documents (A), Finnhub Insiders (B), Internal Smart Money Tape (C).

## Required workflow changes at integration time
- Use the prepared Node-24 workflow proposal: `actions/checkout@v6`, `actions/setup-python@v6`, `actions/upload-artifact@v6`, `peter-evans/create-pull-request@v8`.
- Add `V183_RUN_ID` while retaining V18.2 artifacts during migration.
- Verify Smart Money audit JSON before opening the automated PR.
- Do not publish event raw documents containing unnecessary personal information; persist only fields needed for scoring/audit.
