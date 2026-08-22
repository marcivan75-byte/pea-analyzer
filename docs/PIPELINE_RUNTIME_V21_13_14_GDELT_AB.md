# V21.13.14 — GDELT grouped A/B from existing PIT baseline

## Objectif

Mesurer le gain réel du regroupement GDELT V21.13.13 sans refaire les requêtes individuelles et sans modifier le PREMARKET/POSTMARKET de production.

## Principe

Le runner manuel lit le dernier snapshot V24.4.2 déjà conservé dans `state/tct_context/TCT_V24_4_2_CATALYST_LEDGER.csv` pour la phase demandée (`PREOPEN` ou `POSTMARKET`). Cette preuve PIT constitue la baseline individuelle.

Aucune nouvelle requête individuelle n'est lancée. Seules les requêtes groupées SHADOW sont émises. Le coût expérimental attendu pour 40 candidats avec `group_size=5` est donc 8 requêtes groupées avant tout diagnostic de fallback, contre 48 requêtes si la baseline individuelle était recollectée.

## Garde-fous

- workflow `workflow_dispatch` uniquement ; aucun `schedule` ni `cron` ;
- restauration read-only du contexte TCT existant ; aucune sauvegarde de cache/state ;
- dépendances minimales via `requirements-catalyst.txt` ;
- même phase, mêmes ISIN et même fenêtre PIT que la baseline enregistrée ;
- arrêt avant réseau si la baseline est absente ou trop ancienne pour la fenêtre GDELT configurée ;
- `new_individual_requests = 0` ;
- aucune autorité de promotion ;
- influence décisionnelle et score = 0 ;
- aucune activation production.

## Comparaison

Le comparateur V21.13.13 conserve l'exigence fail-closed d'équivalence exacte sur :

- erreur fournisseur ;
- nombre d'articles ;
- magnitude ;
- direction ;
- types d'événements ;
- top headlines.

Toute divergence produit un fallback ISIN dans le rapport. Le gain projeté doit être évalué après prise en compte de ce fallback, et non sur le seul ratio théorique 40 → 8.

## Sorties

- `outputs/audit/GDELT_GROUPED_AB_V21_13_14.json`
- `outputs/audit/GDELT_GROUPED_AB_V21_13_14_ROWS.csv`

Le JSON expose notamment : `exact_equivalence_rate`, `news_presence_recall`, `critical_event_recall`, `fallback_count`, `projected_request_reduction_pct`, `new_individual_requests`, `production_activation`.

## Règle de promotion

La présence de ce harness dans `main` ne vaut pas activation. Une éventuelle modification du PREMARKET/POSTMARKET nécessite des A/B représentatifs sur PREOPEN et POSTMARKET, une équivalence jugée suffisante au regard des critères de sécurité définis, et une PR séparée. V21.13.14 ne change aucun chemin de production.
