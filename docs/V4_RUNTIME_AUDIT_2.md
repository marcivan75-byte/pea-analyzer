# Optimisation de durée V4 — audit 2/3 — correction

Date : 2026-08-24

## Modification

- Le gate écrit désormais `CI_SELECTION_ALL_V4.csv`, qui conserve toutes les lignes enrichies dans l'ordre amont.
- CI Light accepte un mode explicite `reuse_selection_context=True`.
- Le runner et la CI utilisent ce mode après un gate réussi.
- La collecte réseau reste disponible pour l'exécution autonome de CI Light.
- Chaque phase expose sa durée : chargement, master, collecte, évaluation et écriture.

## Effet structurel

- Passes réseau avant : **2**.
- Passes réseau après : **1**.
- Tentative répétée sur une identité/page non résolue : supprimée.
- Réécriture intermédiaire des preuves source : supprimée pour la phase Light.
- Champs décisionnels ou de preuve supprimés : **0**.

## Validation ciblée

- 20 tests ciblés : **PASS**.
- Ruff : **PASS**.
- mypy sur les trois modules d'orchestration : **PASS**.

Statut : **PASS — redondance supprimée sans modifier le contrat autonome**.
