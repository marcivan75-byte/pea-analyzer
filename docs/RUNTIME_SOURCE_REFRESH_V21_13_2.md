# Runtime Source Refresh V21.13.2

## Objectif

Réduire le temps réseau des trois pôles les plus coûteux sans réduire l'univers analysé et sans modifier les critères, poids, seuils, scores ou règles de décision.

## Yahoo fundamentals Actions

- Univers de scoring inchangé : jusqu'à 1 829 Actions canoniques.
- Cache persistant : `state/provenance/source_cache/YFINANCE_INFO_V1.json`.
- Priorités de rafraîchissement : HOT / WARM / COLD.
- TTL par défaut : 3 / 7 / 21 jours.
- Budget normal : 450 titres par run ; les titres sans cache restent obligatoires au bootstrap.
- Âge maximum admissible : 35 jours ; au-delà, la donnée n'est pas utilisée comme cache valide.
- Cache négatif : 7 jours.
- Les timestamps de source sont conservés : une donnée de cache n'est jamais re-datée au jour du run.
- PER TTM, PER forward et P/B peuvent être recalculés localement à partir du dernier OHLCV et des dénominateurs fondamentaux mis en cache.

## Finnhub consensus

- Cache persistant existant conservé ; migration V1 vers format interne V2 sans perte volontaire de couverture.
- Priorités HOT / WARM / COLD.
- Recommandations et objectifs de cours ont des TTL et budgets distincts.
- HOT : recommandation 3 j, target 7 j.
- WARM : recommandation 7 j, target 14 j.
- COLD : recommandation 14 j, target 28 j.
- Budgets recommandation : 120 / 80 / 40.
- Budgets target : 60 / 40 / 20.
- Les timestamps recommandation et target sont indépendants.
- Une indisponibilité du endpoint target n'interrompt pas la collecte des recommandations.
- Le fail-fast authentification/entitlement reste actif.

## ETF Fund Flows

- Le collecteur validé reste inchangé dans sa logique de données et de calcul.
- Le runner répartit l'univers en lots bornés et utilise au maximum 8 workers.
- Taille de lot par défaut : 18 instruments.
- L'historique reste append-only par `(instrument_id, as_of)` avec arbitrage de source existant.
- Aucune reconstruction d'historique n'est déclenchée par l'optimisation.
- La télémétrie est écrite dans `outputs/audit/ETF_FUND_FLOW_COLLECTION_RUNTIME_V21_13_2.json`.

## Gouvernance

- Aucun titre n'est supprimé du scoring pour accélérer le run.
- Aucune valeur manquante n'est inventée ou imputée en neutre.
- Aucun poids, seuil ou signal d'entrée/sortie n'est modifié.
- T1/T2 restent exclusivement Action TCT.
- ETF Fund Flows reste SHADOW et n'acquiert aucune influence décisionnelle.
- Les caches ne changent que la fréquence des appels réseau ; ils ne changent pas la sémantique de la donnée.

## Validation

La promotion sur `main` exige : compilation, Ruff, audit statique, audits d'intégrité, suite pytest complète et validation GitHub verte. Les gains de temps réels doivent être mesurés sur les prochains runs grâce aux audits de cache et de collecte ; les estimations ne sont pas considérées comme une preuve de performance.
