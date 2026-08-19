# TCT — Correction de périmètre V24.3.0

Date : 19/08/2026

## Décision

Le besoin fonctionnel est clarifié ainsi :

> Enrichir la démarche TCT quotidienne, sur un horizon de quelques jours à approximativement une semaine, avec des outils utilisés par les traders court terme pour améliorer les décisions d'entrée et de sortie. Il ne s'agit pas de faire du day trading.

## Ce qui est abandonné

La piste V24.2.x a interprété trop littéralement l'inspiration day trading en introduisant une couche intraday 5 minutes.

Sont retirés du runtime actif :

- téléchargements 5 minutes ;
- cache `actions_intraday_5m` ;
- VWAP de séance ;
- opening range intraday ;
- score micro-timing 5 minutes ;
- spread/order-flow/carnet intraday ;
- ledgers de sessions intraday ;
- analytics spécifiques V24.2.x.

Les fichiers historiques restent accessibles dans l'historique Git mais ne font plus partie de la version active du projet.

## Ce qui est conservé conceptuellement

Les idées suivantes sont adaptées à l'échelle quotidienne/hebdomadaire :

- liquidité → turnover daily ;
- RVOL → volume quotidien relatif ;
- accélération volume → moyenne courte vs moyenne 20 jours ;
- volatilité → ATR et expansion/compression daily ;
- breakout → plus hauts 20/55 jours ;
- retest → retour sur niveau de cassure dans les séances suivantes ;
- qualité d'exécution → éviter titres trop illiquides, gaps et sur-extensions ;
- niveaux clés → pivots daily, niveaux semaine précédente, support/résistance ;
- tendance/momentum → EMA 9/20 et rendements 5/20 jours ;
- VWAP conceptuel → prix roulant pondéré par volume 20/60 jours, explicitement distinct d'un VWAP de séance ;
- contexte supérieur → alignement weekly dérivé des données daily ;
- discipline de sortie → failed breakout, distribution volume, cassure de tendance, détérioration weekly.

## Coût

V24.3.0 ne nécessite aucun abonnement ou flux supplémentaire et n'appelle aucune collecte de marché additionnelle.

Il réutilise exclusivement le cache OHLCV quotidien déjà nécessaire au système.

## Gouvernance

- Production V21.8.1 inchangée.
- V24.3.0 = `SHADOW_RESEARCH_ONLY`.
- Influence décision/score/sizing/stop/CT = 0.
- Holdout final fermé.
- Aucun ordre réel.
- Aucun TP/SL fixe promu.
- CT gelé jusqu'à clôture du chantier TCT.
- Aucun résultat V24.2.x ne peut servir de preuve de performance.

## Critère de réussite

V24.3.0 ne sera retenue que si un test PIT/OOS montre que ces filtres daily/weekly améliorent réellement la qualité des entrées et/ou sorties TCT avec une espérance et un profil de risque supérieurs à la baseline.
