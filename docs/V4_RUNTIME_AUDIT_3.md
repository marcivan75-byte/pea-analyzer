# Optimisation de durée V4 — audit 3/3 — équivalence et gain

Date : 2026-08-24

## Protocole contradictoire

Pour chaque itération, CI Light est générée une première fois par collecte autonome puis une seconde fois par réutilisation du contexte du gate. Une empreinte SHA-256 est calculée sur les identités, décisions, raisons, métriques Boursorama, états TradingView, Morningstar et URLs.

## Résultat observé

- Empreintes décisionnelles autonome/réutilisée : identiques.
- Lignes comparées : 15/15.
- Perte d'information : **aucune**.
- Phase CI Light médiane avec réutilisation : **0,174 s**.
- Phase CI Light médiane autonome : **0,875 s**.
- Gain médian de la phase Light : **80,11 %**.
- Couple gate + Light chaud : environ **1,04 s** contre **1,52 s**, soit environ **32 %** de gain.
- Moyenne mixte mesurée après correction : **1,694 s**, contre **2,259 s** avant correction, soit environ **25 %** de gain.

Les temps réseau restent variables; le gain structurel certain est la suppression d'une passe complète de collecte sur deux.

Statut : **PASS — durée réduite et information décisionnelle byte-identique**.

Empreinte commune des trois comparaisons : `2d33e83d5b74c200261819cad39f155ca6a620cd3253a2fb202e16321eabbd13`.

Suite complète après optimisation : **902 tests et 7 sous-tests PASS**; Ruff, compilation et mypy ciblé PASS.
