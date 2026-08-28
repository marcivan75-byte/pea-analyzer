# Process HEBDO actualisé — Weekly Operational V4.4 FINAL

Date : 2026-08-28
Commit de référence opérationnel : voir `main` (`ohlcv incremental 10d` + cache Actions v4)
Runner : `python -m v182.reporting.weekly_operational_runner_v4_4`
Workflow : `.github/workflows/committee_master_daily.yml` (`PEA Weekly Heavy Committee V4.4`)
Cadence : vendredi 18:30 Europe/Paris
Ordres réels : interdits

## Objectif

Un seul chemin Friday, reproductible, fail-closed, sous 20 minutes cible, sans retuning des critères / poids / seuils.

## DAG

1. Restore cache OHLCV v4 (`data/cache/actions` + `data/cache/etf`, pas yfinance)
2. Restore état décisionnel + research
3. Préparer cache yfinance inscriptible + `PEA_YF_INCREMENTAL_PERIOD=10d`
4. Identity hydration
5. Runner V4.4
   - core V22.2.3 (append OHLCV 10j si cache compatible)
   - tail critique V21.16.0 (`PEA_WEEKLY_CRITICAL_ONLY=1`)
   - overlay V4 (pas de recompute upstream, pas de 2e CI Light)
   - O/R V1 simulation
   - O/R challenger 50/30/20 × risk_soft_mult
   - Sector O/R SHADOW
   - Portfolio budget SHADOW
   - Publication CI / HEBDO O/R datée + alias LATEST
   - Daily O/R SHADOW (skip si pas de LATEST)
   - Rapports `OR_HEBDO_REPORT.md` / Android
6. Summaries + artefact `committee-weekly-v4-4-*`
7. Save OHLCV si les deux manifests existent (indépendant du gate métier)

## Modes source

- Défaut vendredi : `PEA_SLOW_SOURCE_MODE=CACHE_PREFERRED`
- Maintenance manuelle seulement : input `maintenance_full_refresh` → `LIVE`

## Cache OHLCV

- Clé : `ohlcv-v4-${{ github.run_id }}` (restore aussi `ohlcv-v3-`)
- Périmètre persisté : parquets + manifests Actions/ETF uniquement
- Refresh incrémental Friday : 10 jours, pas de full 5 ans
- Skip réseau si le manifest a déjà été écrit le jour UTC courant

## Sorties comité (production inchangée)

- COMMITTEE_DECISIONS / SECTOR_RANKING / CRITERIA_COVERAGE / SUMMARY
- Decision brief + Android control center

## Sorties O/R (SHADOW, influence 0)

- OR_RANKING_HEBDO_SHADOW_{date}.csv + alias LATEST
- OR_RANKING_HEBDO_SHADOW_ETF_ONLY_{date}.csv
- OR_RANKING_HEBDO_SHADOW_ACTION_CT_ONLY_{date}.csv
- OR_RANKING_ETF_MT_SHADOW_{date}.csv
- SECTOR_OR_RANKING_SHADOW_{date}.csv + SECTOR_OR_AGGREGATE_{date}.csv
- OR_HEBDO_FORWARD_VALIDATION_SCHEDULE.csv
- OR_RANKING_DAILY_SHADOW_{date}.csv si input Daily présent
- outputs/mobile/OR_HEBDO_REPORT.md

## Non négociable

- WIP=1 sur ce process
- missing ≠ 50
- BLOCK_DATA non rouvert
- T1/T2 hors ACTION TCT
- holdout fermé
- O/R ne modifie ni selection_score ni entry_confidence
- pas de promotion automatique OR_READY → BUY
