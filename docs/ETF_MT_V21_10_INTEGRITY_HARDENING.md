# ETF MT — durcissement d’intégrité V21.10

Ce portage reprend uniquement les correctifs V20.9.4 encore nécessaires dans l’architecture V21.x. Il ne remplace ni Sector Rotation V2, ni ETF Fund Flows V1, ni la gouvernance d’entrée/sortie V21.8.

## Correctifs intégrés

- Les longueurs d’historique, dates de fraîcheur et gates du scoring ETF MT sont calculées après suppression des lignes de padding d’un batch Yahoo dont `Close` est absent/non numérique. Un index commun multi-tickers ne peut donc plus transformer des séances pré-lancement en historique exploitable.
- Le référentiel V20.8.1 conserve exactement ses 38 critères PIT et ses poids historiques. Toute dérive du nombre de critères, du total de poids, du blend 55/45, du seuil 82 ou du Top 2 bloque le wrapper d’intégrité.
- L’ancienne sortie `+4 % / -18 % / H168` reste conservée uniquement pour la reproductibilité des anciens backtests. Elle est traitée comme `BACKTEST_REPLAY_ONLY`, sans routage d’ordre et non opérationnelle sous V21.8.
- Les candidats issus du moteur historique sont exposés comme `REFERENCE_CANDIDATE`, jamais comme `BUY_CANDIDATE`.
- T1/T2 restent interdits pour ETF MT ; les critères structurels ne peuvent pas promouvoir le signal historique.

## Ce qui n’est pas promu

Le diagnostic local de sortie sans TP fixe (budget de risque autour de 7 %, trailing) reste une recherche sur validation consommée. Il n’est pas porté comme règle opérationnelle. Le holdout final à partir du 10 février 2026 reste fermé. La gouvernance V21.8 demeure l’autorité courante pour les entrées/sorties.

## Compatibilité V21.10

La couche TER/AUM V21.10 reste indépendante du score historique attribué à V20.8.1. Les données structurelles peuvent être collectées et auditées sans modifier l’attribution historique des 38 critères ni créer un signal d’achat.

Le workflow ETF MT autonome est déjà limité à `workflow_dispatch`; aucun schedule ETF concurrent n’est ajouté. Le Comité unifié reste l’orchestrateur planifié.
