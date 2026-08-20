# V21.14 — ETF PEA CT / LT SHADOW

## Objet

Rendre exécutables en diagnostic les horizons ETF CT et LT déjà présents dans le référentiel V20.7.1, sans leur attribuer une performance non démontrée, sans modifier le moteur MT V20.8.1 et sans ouvrir de holdout.

## Source des poids

Les poids et directions CT/LT restent exactement ceux de `config/V20_7_1_ETF_CRITERIA_REGISTRY.json`. V21.14 ne retune aucun poids ni seuil.

Les anciens seuils 77 / 70 sont conservés uniquement comme **bandes de contexte** (`HIGH_SCORE_CONTEXT`, `WATCH_CONTEXT`) et ne constituent jamais une règle de BUY ou SELL.

## Données et score

- entrée : snapshot ETF enrichi du run courant `outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv` ;
- univers exigé : 102 ETF PEA / 102 ISIN uniques ;
- score : rang percentile cross-sectionnel sur chaque critère numérique observé ;
- direction HIGH/LOW issue du référentiel ;
- critère absent = poids absent du dénominateur ; aucune imputation neutre ;
- score ETF calculé uniquement si sa couverture pondérée observée atteint 70 % ;
- `distribution_policy` reste non numérique : aucune conversion ACC/DIST n'est inventée sans définition gouvernée ;
- un critère doit être observé sur au moins 20 % de l'univers (21 ETF sur 102) pour pouvoir produire des percentiles ; en dessous, il est exclu du score de tous les ETF pour ce run.

Le seuil cross-sectionnel de 20 % est un **gate de qualité de données**, pas un poids ni un seuil de décision. Il empêche notamment qu'une seule observation disponible obtienne artificiellement un percentile de 100.

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

## Articulation avec V21.13

V21.14 ne lance aucune nouvelle calibration. Lors d'un futur backtest PIT/OOS CT/LT :

- seuls les instruments `PRIMARY_FULL_FROM_ANCHOR` pourront entrer dans la calibration principale tant qu'aucune politique gouvernée de date de listing/inception n'a été validée ;
- seuls les instruments `STRESS_FULL_2020_2022` pourront entrer dans la bibliothèque de stress ;
- le poids de calibration ordinaire du stress reste 0 ;
- les historiques incomplets ne seront ni imputés ni assimilés à de nouvelles cotations sans preuve.

## Fréquence

Le module est exécuté après la collecte/enrichissement quotidienne existante. Aucun nouveau cron n'est créé : il réutilise le workflow `committee_tct_ct_daily.yml` déjà planifié.

## Sorties

- `outputs/etf_ct_lt_shadow/ETF_CT_LT_SHADOW.csv` ;
- `outputs/audit/ETF_CT_LT_V21_14_SHADOW.json` ;
- `outputs/mobile/ETF_CT_LT_SHADOW.md`.

Ces sorties sont diagnostiques et ne mutent aucun fichier de décision du Comité.
