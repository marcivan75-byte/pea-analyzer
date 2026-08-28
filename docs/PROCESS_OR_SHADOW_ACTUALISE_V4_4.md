# Process actualisé — Ranking Objectifs / Risques SHADOW

Version process : OR_SHADOW_PROCESS_V4_4
Ancré sur : Weekly Operational V4.4
Date : 2026-08-28

## Couches (aucune n’écrit dans les scores production)

1. Sélection horizon — CT / MT / TCT / ETF 38 (inchangé).
2. Contexte — Rotation V2, régime, news TCT (influence 0 ou soft-cap SHADOW).
3. Simulation O/R V1 — entrée optimale, invalidation, R/R, fiabilité.
4. Ranking 50/30/20 × risk_soft_mult — post-sélection, post entry-confidence.
5. Publication — CSV datés + jointure comité diagnostique.
6. Comité production — COMMITTEE_DECISIONS inchangé.

## DAG Friday

core V22.2.3
  → tail critique V21.16.0 (PEA_WEEKLY_CRITICAL_ONLY=1)
  → overlay V4 (sans recompute upstream)
  → objectives_risk_shadow_v1
  → objectives_risk_challenger_v2
  → sector_or_shadow_v1
  → portfolio_budget_challenger_v2
  → ci_challenger_publication_v2
  → or_ranking_daily_shadow_v1

## Formule (gelée)

OR_COMPOSITE = (0.50×sélection + 0.30×rr_score + 0.20×fiabilité) × risk_soft_mult

- cible R/R 2.0 → 70 ; cap 4.0 → 100
- risk_soft_mult : GREEN 1.00 / AMBER 0.85 / ORANGE 0.70 / MISSING 0.55 / RED 0.40
- toute modification de poids = nouvelle epoch documentée

## Gates HEBDO

- INSUFFICIENT_ENTRY_PROOF → ATTENDRE_REPLI_SHADOW (jamais READY O/R)
- risque ORANGE/AMBER/MISSING → soft-cap
- BLOCK_DATA comité → NON_ACTIONNABLE
- ETF hors régime requis ou STALE / history courte → NON_ACTIONNABLE
- SOURCE_FAILURE critique ou < 3 sources valides → AUDIT_ONLY_FAIL_CLOSED

## Artefacts vendredi

- outputs/committee_master/OR_RANKING_HEBDO_SHADOW_{date}.csv
- ..._COMBINED_{date}.csv
- ..._ETF_ONLY_{date}.csv
- ..._ACTION_CT_ONLY_{date}.csv
- OR_RANKING_ETF_MT_SHADOW_{date}.csv
- OR_RANKING_HEBDO_SHADOW_TOP15_ACTION_{date}.csv
- OR_RANKING_HEBDO_SHADOW_TOP15_ETF_{date}.csv
- SECTOR_OR_RANKING_SHADOW_{date}.csv + SECTOR_OR_AGGREGATE_{date}.csv
- state/objectives_risk/OR_HEBDO_FORWARD_VALIDATION_SCHEDULE.csv
- Daily si présent : OR_RANKING_DAILY_SHADOW_{date}.csv

## Daily

Même formule. Si LATEST Daily absent : SKIPPED_NO_DAILY_INPUT, pas d’imputation.

## Interdits jusqu’à OOS ≥ 8 runs HEBDO + revue humaine

- modifier les poids V21 / CI V22.2
- malus R/R dans le score de référence
- promotion OR_READY → BUY live
- TP/SL fixes dérivés du module
- ouvrir le holdout
- activer tracking error ETF
- activer quality/theme V22.1 sans evidence_sufficient
