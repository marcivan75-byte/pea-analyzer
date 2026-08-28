# Process HEBDO actualisé — Weekly Operational V4.4 FINAL

Date : 2026-08-28
Runner : `python -m v182.reporting.weekly_operational_runner_v4_4`
Workflow : `.github/workflows/committee_master_daily.yml` (`PEA Weekly Heavy Committee V4.4`)
Cadence : vendredi 18:30 Europe/Paris
Ordres réels : interdits

## Objectif

Un seul chemin Friday, reproductible, fail-closed, sous 20 minutes cible, sans retuning des critères / poids / seuils.

## DAG

1. Restore caches OHLCV + état décisionnel + research
2. Préparer cache yfinance inscriptible
3. Identity hydration
4. Runner V4.4
   - core V22.2.3
   - tail critique V21.16.0 (`PEA_WEEKLY_CRITICAL_ONLY=1`)
   - overlay V4 (pas de recompute upstream, pas de 2e CI Light)
   - O/R V1 simulation
   - O/R challenger 50/30/20 × risk_soft_mult
   - Sector O/R SHADOW
   - Portfolio budget SHADOW
   - Publication CI / HEBDO O/R datée
   - Daily O/R SHADOW (skip si pas de LATEST)
5. Summaries + artefacts `committee-weekly-v4-4-*`
6. Save caches si gates OK

## Modes source

- Défaut vendredi : `PEA_SLOW_SOURCE_MODE=CACHE_PREFERRED`
- Maintenance manuelle seulement : input `maintenance_full_refresh` → `LIVE`

## Sorties comité (production inchangée)

- COMMITTEE_DECISIONS / SECTOR_RANKING / CRITERIA_COVERAGE / SUMMARY
- Decision brief + Android control center

## Sorties O/R (SHADOW, influence 0)

- OR_RANKING_HEBDO_SHADOW_{date}.csv
- OR_RANKING_HEBDO_SHADOW_ETF_ONLY_{date}.csv
- OR_RANKING_HEBDO_SHADOW_ACTION_CT_ONLY_{date}.csv
- OR_RANKING_ETF_MT_SHADOW_{date}.csv
- SECTOR_OR_RANKING_SHADOW_{date}.csv + SECTOR_OR_AGGREGATE_{date}.csv
- OR_HEBDO_FORWARD_VALIDATION_SCHEDULE.csv
- OR_RANKING_DAILY_SHADOW_{date}.csv si input Daily présent

## Non négociable

- WIP=1 sur ce process
- missing ≠ 50
- BLOCK_DATA non rouvert
- T1/T2 hors ACTION TCT
- holdout fermé
- O/R ne modifie ni selection_score ni entry_confidence
- pas de promotion automatique OR_READY → BUY
