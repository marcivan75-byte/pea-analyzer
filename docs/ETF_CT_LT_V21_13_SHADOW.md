# V21.13 — ETF PEA CT / LT SHADOW

## Objet

Rendre exécutables les horizons ETF CT et LT déjà présents dans le référentiel V20.7.1, sans leur attribuer une performance non démontrée et sans modifier le moteur MT V20.8.1.

## Source des poids

Les poids et directions CT/LT restent exactement ceux de `config/V20_7_1_ETF_CRITERIA_REGISTRY.json`. V21.13 ne retune aucun poids ni seuil.

Les anciens seuils 77 / 70 sont conservés uniquement comme **bandes de contexte** (`HIGH_SCORE_CONTEXT`, `WATCH_CONTEXT`) et ne constituent jamais une règle de BUY.

## Données et score

- entrée : snapshot ETF enrichi du run courant `outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv` ;
- score : rang percentile cross-sectionnel des 102 ETF PEA sur chaque critère numérique observé ;
- direction HIGH/LOW issue du référentiel ;
- critère absent = poids absent du dénominateur ; aucun score neutre n'est injecté ;
- score calculé uniquement si la couverture pondérée observée atteint 70 % ;
- `distribution_policy` reste non numérique : aucune conversion ACC/DIST n'est inventée sans définition gouvernée.

## Gouvernance

- `SHADOW_ONLY` ;
- influence sur les décisions : 0 ;
- influence sur le score MT de référence : 0 ;
- ordres réels : interdits ;
- promotion : interdite ;
- BUY/SELL : interdits ;
- T1/T2 : interdits, réservés aux Actions TCT ;
- aucune attribution historique de performance CT/LT avant backtest PIT/OOS dédié ;
- le snapshot courant n'est jamais traité comme vérité historique ;
- les holdouts existants ne sont pas ouverts.

## Fréquence

Le module est destiné à être exécuté après la collecte/enrichissement quotidienne existante. Aucun nouveau cron ne doit être créé.

## Sorties

- `outputs/etf_ct_lt_shadow/ETF_CT_LT_SHADOW.csv` ;
- `outputs/audit/ETF_CT_LT_V21_13_SHADOW.json` ;
- `outputs/mobile/ETF_CT_LT_SHADOW.md`.
