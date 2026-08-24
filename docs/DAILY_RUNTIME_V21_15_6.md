# Daily runtime V21.15.6

## Objective

V21.15.6 reduces the GitHub Actions wall time of the Daily tactical workflow without changing production decision criteria, weights, thresholds, T1/T2 formulas, T1/T2 asset/horizon scope, or order policy.

## Daily versus Weekly responsibilities

### Weekly Heavy

The Weekly Heavy run remains the exhaustive research and enrichment pass. It:

- executes WAVE_09 TopDown with its full FRED/GDELT contract;
- evaluates exhaustive research/shadow scopes where required;
- writes the enriched Action and ETF masters;
- persists a validated weekly master snapshot under `state/provenance/weekly_master_snapshot_v1/`;
- retains full-universe Action CT research state.

The weekly snapshot transports the latest weekly W09 values to subsequent Daily runs. W09 itself is not executed by the Daily workflow.

### Daily Tactical

The Daily run:

- performs zero W09 FRED/GDELT network requests;
- starts from a validated Daily fast state or, after Weekly, the validated weekly master snapshot;
- fails closed if neither state exists, instead of rebuilding a master without W09;
- refreshes Daily market/dynamic data through the governed collection path;
- computes the full TCT baseline across the Action universe;
- computes exact T1/T2 only for the current baseline Top-N/minimum-coverage scope;
- preserves a full-universe TCT decision-file shape using non-authoritative `NO_T1_T2` placeholders outside the Daily Top-N;
- runs Action CT V22.0/V22.1 shadow computation only on the upstream Action CT Daily preselection Top-N;
- runs TCT V24.3.1 only from the Daily bounded TCT shadow input;
- limits failed Investing live refresh attempts while retaining the full Investing refresh contract in Weekly;
- isolates Daily Action CT `LATEST` files from the full-universe Weekly `LATEST` state;
- keeps T1/T2 restricted to ACTION TCT only;
- keeps fixed take-profit and legacy fixed-stop logic disabled;
- keeps real orders disabled.

## Cache identity

An unrelated Git commit must not invalidate a valid Daily enriched-master state. V21.15.6 uses:

1. the static data/configuration contract;
2. hashes and row/ISIN integrity of retained Action and ETF masters;
3. a collection-code contract covering the modules that can change collection/merge semantics;
4. the source-cache contract to choose `DELTA_ONLY` versus `RECONCILE_CACHE`.

`GITHUB_SHA` is retained for audit but is not the cache identity.

## Fail-closed bootstrap

Because W09 is Weekly-only, the first V21.15.6 Daily after deployment is forbidden until a Weekly Heavy run has generated a valid `WEEKLY_MASTER_SNAPSHOT_V1` (unless a compatible validated Daily state already exists).

Expected failure message when the seed is absent:

`DAILY_WEEKLY_BASELINE_MISSING: run Weekly Heavy once to seed the validated W09/master snapshot before Daily V21.15.6`

This is intentional data-quality protection, not a runtime fallback.

## Runtime bottlenecks removed from the measured run #8 baseline

The audit of run #8 showed approximately:

- W09 TopDown: 669.7 s;
- Daily TCT core: 238.8 s;
- Action CT/TCT shared shadow bundle: 185.8 s;
- selected-source enrichment: 75.5 s;
- total consolidated runtime: 1189.8 s.

V21.15.6 removes W09 from Daily network execution, reduces exact TCT and Action CT shadow calculation scopes to their operational Daily candidates, bounds the unsuccessful Investing Daily retry path, preserves full decision/audit shape, and prevents unrelated commits from forcing cold-state reconstruction.

## Validation gates

V21.15.6 is not considered runtime-validated until all of the following are true on a representative GitHub run:

- Weekly Heavy succeeds and persists the validated weekly master snapshot;
- first Daily succeeds from `RECONCILE_CACHE` or a compatible Daily state;
- subsequent Daily reaches `DELTA_ONLY` when the source-cache contract is unchanged;
- W09 Daily network calls are zero;
- Action/ETF universe and decision-quality gates pass;
- TCT full decision shape is preserved while exact engine rows are bounded;
- no PIT lineage dtype error occurs;
- final Daily wall time is measured against the project runtime target.

No runtime estimate is treated as validation before these gates are observed in GitHub Actions.
