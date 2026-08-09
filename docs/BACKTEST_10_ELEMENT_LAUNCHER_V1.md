# BACKTEST 10 ELEMENTS V1

## Finalité

Ce lanceur enrichit un historique point-in-time roulant, puis exécute dix backtests **consécutifs et indépendants**. Chaque élément repart des pondérations réellement utilisées par son moteur de production et ne reçoit jamais les poids optimisés par l'élément précédent.

Aucune pondération de production n'est modifiée automatiquement.

## Les 10 éléments

### ETF PEA 102 — V20.7

1. `ETF_SHORT` — 14 jours, direction baissière, 10 candidats.
2. `ETF_CT` — 28 jours, direction haussière, 10 candidats.
3. `ETF_MT` — 91 jours, direction haussière, 10 candidats.
4. `ETF_LT` — 365 jours, direction haussière, 10 candidats.
5. `ETF_TOPDOWN` — 91 jours, optimisation indépendante des 7 pondérations de contexte : global macro, country macro, global news, country news, sector news, instrument news, market sentiment.

Les quatre horizons directs ETF reprennent `V20.7_ETF102_CONFIG.json`. Le Top-Down reprend `V20.7_FUNNEL_CONFIG.json`. `ETF_SHORT` inverse la cible de rendement afin qu'un score SHORT élevé soit récompensé lorsqu'il anticipe effectivement une baisse.

### Actions PEA 1429 — V21.0

6. `ACTION_T1_1_4W` — 28 jours ; moteur CT V21.0, 20 critères, top 20.
7. `ACTION_T2_1_3M` — 91 jours ; moteur MT V21.0, 23 critères, top 30.
8. `ACTION_T3_3_6M` — 182 jours ; même moteur MT, mais backtest et optimisation totalement indépendants de T2.
9. `ACTION_T4_6_12M` — 365 jours ; moteur LT V21.0, 22 critères, top 30.
10. `ACTION_T5_12_24M` — 730 jours ; même moteur LT, mais backtest et optimisation totalement indépendants de T4.

Le mapping respecte le comité V21.0 : T1 utilise CT, T2/T3 utilisent MT, T4/T5 utilisent LT. Les critères détaillés couvrent notamment technique, momentum, consensus et variation à 4 semaines, upgrades, révisions pondérées brokers, valorisation, qualité, croissance, endettement, risque, dividendes et liquidité.

## Enrichissement de l'historique

Le workflow recharge d'abord le dernier artifact `BACKTEST-10-ELEMENT-HISTORY-*`, puis récupère les artifacts validés disponibles :

- `V20.7-ETF-PEA-102-Committee-*` ;
- `V21.0-Actions-PEA-1429-Reference-*` ;
- les artifacts V20.4 Actions encore disponibles comme historique de secours.

Pour V21.0 Actions, les scores élémentaires 0..100 ne sont **pas recalculés avec le code actuel**. Ils sont restaurés à partir des couples `contrib_* / effective_weight_*` archivés par le comité à la date du run. Cette méthode préserve la logique point-in-time même si le moteur évolue ultérieurement.

Les rendements futurs sont calculés uniquement à partir d'un prix observé dans un snapshot ultérieur du même instrument. Aucun critère historique n'est reconstruit avec une information future.

L'archive complète est réémise à chaque run avec une rétention de 90 jours. Tant que le lanceur s'exécute régulièrement, l'historique roulant survit donc à l'expiration des artifacts sources.

## Politique données manquantes

Les dix backtests utilisent `RENORMALIZE_OBSERVED` :

- aucune substitution automatique par 50 ;
- score calculé uniquement sur les critères observés ;
- poids renormalisés sur les critères disponibles ;
- ligne exclue si la couverture pondérée est inférieure au seuil défini.

Cette règle est alignée sur V20.7 ETF et V21.0 Actions.

## Optimisation

Pour chaque élément :

1. sélection de l'horizon de rendement propre à l'élément ;
2. reconstruction des critères point-in-time ;
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

## Hard gates

Le run échoue si :

- il n'y a pas exactement 5 tests ETF + 5 tests Actions ;
- une erreur technique apparaît dans un des 10 éléments ;
- l'historique ne contient pas au moins 100 lignes ETF et 1 000 lignes Actions après enrichissement ;
- moins de 60 composantes V21.0 Actions sont restaurées ;
- un module tente de modifier des pondérations de production.

`INSUFFICIENT_HISTORY` n'est pas une erreur : c'est le verdict normal tant qu'un horizon ne dispose pas encore d'assez de rendements futurs réellement observés.

## Exécution

Workflow : `.github/workflows/BACKTEST_10_ELEMENT_LAUNCHER_V1.yml`.

Il est lançable manuellement et planifié chaque vendredi à 19:30 UTC. Le nombre de combinaisons testées par élément est configurable ; la valeur standard est 1 000.
