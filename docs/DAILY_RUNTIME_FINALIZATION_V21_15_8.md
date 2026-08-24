# Daily runtime finalization — V21.15.8

Date: 2026-08-24
Branch: `codex/runtime-boursorama-shared-limiter-v21-15-2`
Official workflow: `.github/workflows/committee_tct_ct_daily.yml` (`PEA Daily Collect + TCT CT V21.8.1`)

## Final validated result

Representative official hot-cache run: `32729470185`.

- Workflow conclusion: SUCCESS on all steps.
- Consolidated runtime: 76.587378 s.
- Collection: 34.927690 s.
- ETF structure replay: 1.286539 s.
- Tactical DAG: 19.316414 s.
- CI restitution: 12.907805 s.
- Provenance compact-cache persistence: 8.147163 s.
- Provenance compact-cache load: `HIT_EXACT`, 2.684588 s including exact ledger SHA-256 validation.
- Wave09 Daily network calls: 0; cadence remains WEEKLY_ONLY.
- Decision logic, criteria, weights and thresholds unchanged.
- T1/T2 scope unchanged and restricted to Action TCT.
- Real orders disabled.
- CI restitution: 3760 decision rows retained; detailed rows limited to BUY_CANDIDATE + WATCH; reference completeness true; score/decision mutation false.
- CI outputs generated successfully: Word, Excel and Android summary.

Additional stability confirmation: official run `32729723228` completed SUCCESS on all workflow steps after the hot-cache validation.

## Runtime changes retained

- Delta-only Daily collection and weekly-only Wave09 network refresh.
- Bounded Daily TCT/Action CT tactical scope while preserving weekly full-universe research.
- Chunked and bounded CI provenance handling.
- Exact persistent compact provenance cache with fail-safe fallback to the authoritative full ledger scan on any contract, ledger hash or cache-integrity mismatch.
- Same financial model and governance invariants preserved.

## Diagnostic cleanup

Temporary Daily dispatch bridge removed after final validation:

- `.github/workflows/_temporary_daily_dispatch.yml` removed.
- `.github/daily-manual-trigger` removed.
- `.github/daily-dispatch-status.json` removed.
- Temporary diagnostic PR #188 closed without merge.

The official Daily workflow remains the production entry point.
