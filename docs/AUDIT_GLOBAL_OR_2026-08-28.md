# Audit global PEA Analyzer — Objectifs / Risques + HEBDO V4.4

Date : 2026-08-28  
Périmètre : notes O/R 26–27/08 + dépôt `marcivan75-byte/pea-analyzer` (main)  
Statut : SHADOW_RESEARCH_ONLY — `real_orders_enabled = false` — influence décision = 0

## 1. Verdict

Le couple Objectif/Risque n’est **plus un diagnostic orphelin**. Sur main, le pipeline Friday V4.4 exécute déjà :

1. simulation O/R V1 (`objectives_risk_shadow_v1`)
2. ranking challenger 50/30/20 × `risk_soft_mult` (`objectives_risk_challenger_v2`)
3. contexte sectoriel SHADOW (`sector_or_shadow_v1`)
4. publication datée HEBDO / ETF / ACTION CT / top-N + calendrier forward (`ci_challenger_publication_v2`)

L’écart documenté dans les Word (moteur existant mais non branché) est **clos côté HEBDO**.
Les écarts restants sont de **maturité** (OOS 8 semaines, Daily isolé, matrice ETF 268 / TE, retuning poids V21) — pas d’orchestration.

## 2. Cartographie audit ↔ runtime

| Module audit | Constat Word | État dépôt 28/08 |
|---|---|---|
| HEBDO / Weekly V4 | Adapter post CI V22.2, CSV datés | Branché dans `weekly_operational_runner_v4_4` |
| Formule 50/30/20 | Poids verrouillés | `config/OBJECTIVES_RISK_CHALLENGER_V2.json` |
| Labels OR_* | READY / WATCH_PRIORITY / WATCH / HOLD | Colonne `OR_HEBDO_LABEL` |
| Actions SHADOW | READY_RESEARCH_ONLY / SURVEILLER / ATTENDRE_REPLI / NON_ACTIONNABLE | Colonne `OR_ENTRY_ACTION_SHADOW` |
| Gate INSUFFICIENT_ENTRY_PROOF | Interdiction READY O/R | `OR_HEBDO_GATE_REASON` |
| ETF MT 38 | Mapping Sharpe/Sortino/DD + history/stale | `_attach_etf_mt_context` |
| Sectoriel | Voie SHADOW 60–70 %, agrégat, NO_CHASE cap | `sector_or_shadow_v1` |
| Comité | Jointure diagnostique | Publication CI/CI LIGHT enrichie, `reference_modified=false` |
| Daily CT | Adapter ct_daily + CSV Daily | Publication fail-closed `or_ranking_daily_shadow_v1` |
| Sources Boursorama | Fail-closed NO_DETERMINISTIC_CODE | `OR_WEEKLY_SOURCE_GATE` |
| Forward 5/10/20 séances | Protocole recherche | `OR_HEBDO_FORWARD_VALIDATION_SCHEDULE.csv` |

## 3. Invariants vérifiés

- influence score/sizing/stop = 0
- `real_orders_enabled = false`
- Fail-closed : missing ≠ 50 ; BLOCK_DATA non rouvert
- Poids / seuils CI V22.2 et V21.0 non retunés
- T1/T2 hors HEBDO CT/MT
- Holdout fermé

## 4. Hors scope jusqu’à OOS

Retuning momentum V21, malus R/R dans le score de référence, TE ETF, quality/theme V22.1, promotion live.
