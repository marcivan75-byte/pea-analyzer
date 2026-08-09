# BACKTEST 10 ELEMENTS V1

## Finalité

Ce lanceur enrichit un historique point-in-time roulant, puis exécute dix backtests **consécutifs et indépendants**. Chaque élément repart de ses pondérations de production et ne reçoit jamais les poids optimisés par l'élément précédent.

Aucune pondération de production n'est modifiée automatiquement.

## Les 10 éléments

### ETF PEA 102

1. `ETF_SHORT` — 14 jours, direction baissière, 10 candidats.
2. `ETF_CT` — 28 jours, direction haussière, 10 candidats.
3. `ETF_MT` — 91 jours, direction haussière, 10 candidats.
4. `ETF_LT` — 365 jours, direction haussière, 10 candidats.
5. `ETF_TOPDOWN` — 91 jours, optimisation indépendante des 7 pondérations de contexte : global macro, country macro, global news, country news, sector news, instrument news, market sentiment.

Les quatre horizons directs ETF reprennent les pondérations de `V20.7_ETF102_CONFIG.json`. Le Top-Down reprend `V20.7_FUNNEL_CONFIG.json`.

### Actions PEA

6. `ACTION_T1_1_4W` — 28 jours ; pondérations du score court terme.
7. `ACTION_T2_1_3M` — 91 jours ; pondérations du score moyen terme.
8. `ACTION_T3_3_6M` — 182 jours ; même famille de critères que T2, optimisation indépendante.
9. `ACTION_T4_6_12M` — 365 jours ; pondérations du score long terme.
10. `ACTION_T5_12_24M` — 730 jours ; même famille long terme que T4, optimisation indépendante.

## Enrichissement de l'historique

Le workflow recharge d'abord le dernier artifact `BACKTEST-10-ELEMENT-HISTORY-*`, puis récupère les artifacts validés encore disponibles :

- `V20.7-ETF-PEA-102-Committee-*` ;
- `V20.4-GitOK-end-to-end-*` ;
- `V20.4-GitOK-full-cycle-*` ;
- `V20.4-GitOK-reference-*`.

Il conserve les critères exactement tels qu'ils existaient à la date du run et déduplique par date/instrument. Les rendements futurs sont calculés uniquement à partir d'un prix observé dans un snapshot ultérieur du même instrument. Aucun critère historique n'est reconstruit avec une information future.

L'archive complète est réémise à chaque run avec une rétention de 90 jours. Tant que le lanceur s'exécute régulièrement, l'historique ne dépend donc plus de la rétention courte des artifacts sources V20.4/V20.7.

## Politique données manquantes

Les dix backtests utilisent `RENORMALIZE_OBSERVED` :

- aucune substitution automatique par 50 ;
- score calculé uniquement sur les critères observés ;
- poids renormalisés sur les critères disponibles ;
- ligne exclue si la couverture pondérée est inférieure au seuil défini.

Cette règle aligne le backtest ETF sur la politique V20.7 de production.

## Optimisation

Pour chaque élément :

1. sélection de l'horizon de rendement propre à l'élément ;
2. construction des critères point-in-time ;
3. séparation chronologique train / holdout ;
4. génération déterministe de combinaisons bornées ;
5. optimisation multi-objectifs : rendement, drawdown, volatilité, hit-rate et turnover ;
6. shrinkage de l'optimum vers les pondérations actuelles ;
7. validation hors échantillon ;
8. test de sensibilité ±20 % par critère ;
9. recommandation `INCREASE`, `DECREASE`, `KEEP`, `KEEP_CURRENT`, `WAIT_HISTORY` ou `WAIT_FEATURES`.

Un changement n'est recommandé que si l'amélioration hors échantillon franchit le seuil et que la dégradation du drawdown et la dérive totale des poids restent sous les garde-fous.

## Audit final

Le workflow produit :

- `MASTER_RESULTS.csv` — résultat synthétique des 10 tests ;
- `OPTIMIZATION_RECOMMENDATIONS.csv` — recommandation critère par critère et élément par élément ;
- `MASTER_AUDIT.json` — audit machine-readable ;
- `MASTER_AUDIT.md` — synthèse destinée au comité ;
- un sous-dossier par élément avec `WEIGHTS.csv`, `SENSITIVITY.csv`, `LEADERBOARD_TOP250.csv`, `AUDIT.json`, `SUMMARY.md`.

L'audit transversal détecte également les conflits de direction entre horizons. Si un même critère doit être augmenté sur un horizon mais diminué sur un autre, la recommandation reste horizon-spécifique ; aucune modification globale n'est proposée.

## Statuts valides

- `ROBUST_RECOMMENDATION` : optimisation hors échantillon validée ;
- `NO_ROBUST_IMPROVEMENT` : conserver les poids actuels ;
- `INSUFFICIENT_HISTORY` : attendre davantage de snapshots / rendements réalisés ;
- `INSUFFICIENT_FEATURES` : historique trop pauvre pour les critères de cet élément.

`ERROR` est un échec technique et fait échouer le hard gate final du workflow.

## Exécution

Workflow : `.github/workflows/BACKTEST_10_ELEMENT_LAUNCHER_V1.yml`.

Il peut être lancé manuellement et est également planifié chaque vendredi à 19:30 UTC, après les principaux runs de marché. Le nombre de combinaisons testées par élément est configurable à l'exécution ; la valeur standard est 1 000.
