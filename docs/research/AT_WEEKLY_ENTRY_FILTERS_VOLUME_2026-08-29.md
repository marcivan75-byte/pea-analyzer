# AT hebdo Actions — filtres d'entrée uniquement

Date : 2026-08-29  
Branche : `research/at-weekly-v1-20260829`  
Run : `33257651167`  
Sorties : **strictement inchangées**

## Univers

- 1 739 actions valides
- semaines complètes : 2023-01-06 → 2026-08-21
- aucun titre sans volume exploitable

## Comparaison volume — historique complet exploitable

| Variante | Trades | Taux positif | Rendement moyen | Médiane | Profit Factor | Trades 1 semaine | Réussite 1 semaine | Rendement moyen 1 semaine |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline AT V1.1 | 2 489 | 43,43 % | +0,106 % | -0,347 % | 1,038 | 1 353 | 40,06 % | -0,981 % |
| Volume N > N-1 | 1 383 | 43,24 % | -0,140 % | -0,471 % | 0,953 | 740 | 38,24 % | -1,217 % |
| Volume N > MM20 volume | 947 | 44,14 % | +0,073 % | -0,362 % | 1,023 | 499 | 40,28 % | -1,166 % |
| Double volume | 761 | 43,63 % | -0,109 % | -0,358 % | 0,966 | 397 | 39,80 % | -1,328 % |

## Conclusion volume

Aucune des trois formulations ne réduit proprement les faux positifs. Le filtre `volume > MM20 volume` améliore marginalement le taux positif (+0,71 pt), mais diminue le Profit Factor, détériore la perte moyenne des trades d'une semaine et élimine plus de 60 % des trades. Le filtre volume brut ne doit donc pas être promu comme gate obligatoire d'entrée.

## Consensus analystes +20 %

Le référentiel V15.1 définit bien les champs hebdomadaires `analyst_count`, `target_mean` et `upside_mean_pct`, avec clé ISIN/date. Des observations datées existent en juillet/août 2026, mais elles constituent des snapshots live actuels et ne couvrent pas la fenêtre 2023-2026.

Le protocole historique du projet impose explicitement de ne pas rétro-injecter des consensus actuels à des dates historiques. En l'absence d'une archive historique PIT du consensus couvrant la période du backtest, le filtre `upside_mean_pct >= 20 %` ne peut pas être backtesté honnêtement sur 3 ans et 8 mois.

Décision recherche : conserver le filtre consensus comme candidat d'entrée à collecter en forward/PIT, sans fabriquer de résultat rétrospectif.
