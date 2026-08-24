# V4 Hebdo — audit 4/5 — code, orchestration et CI

Date: 2026-08-24

## Périmètre

- Qualité du code V4, séparation des responsabilités et codes de sortie.
- CI Light, règles spécifiques Actions/ETF et exports.
- Workflow GitHub de validation, permissions, reproductibilité et artefacts.

## Constats

1. Le runner V22.2.3 utilisait une chaîne interne `previous.previous...` fragile pour calculer son code de sortie.
2. CI Light V22.2.3 imposait aux ETF le consensus, le nombre d'analystes et le potentiel d'une action; son propre rapport reconnaissait ce choix incorrect.
3. La collecte Boursorama et TradingView était dupliquée entre le gate et CI Light.
4. Le workflow limité existant ne lançait ni la suite complète, ni l'audit des référentiels, ni l'audit de calibration.

## Corrections

- Runner V4 explicite : V22.2.1 → gate de sélection V4 → CI Light V4, avec code de sortie local stable.
- CI Light V4 réutilise l'orchestrateur de sources V4 et interdit toute création de candidat.
- Actions Light : Boursorama positif, plus de 10 analystes, potentiel strictement supérieur à 20 %, et trois horizons TradingView positifs.
- ETF Light : Morningstar au moins 3 étoiles et trois horizons TradingView positifs; aucun contrat analystes Actions.
- URLs exportées uniquement si collectées ou résolues de façon déterministe; pas de lien de recherche libre.
- Workflow V4 à privilèges minimaux, actions épinglées par SHA, concurrence annulable, délai de 30 minutes, dépendances contraintes, lint, compilation, suite complète, gouvernance, calibration, collecte réelle bornée et artefact de preuve.

## Validation

- Tests ciblés code, Light, runner, gate, sources et workflow : **22 PASS**.
- Ruff : **PASS**.
- Contrôles de workflow : actions épinglées, permissions en lecture, périmètre 15 instruments et couches qualité obligatoires.
- Investing : absent du chemin V4 et explicitement désactivé dans les audits.
- Exécution réelle sur 15 titres : **14/15 TradingView** factuels; CI Light publie **0 sélection** car aucun titre ne satisfait simultanément tous ses critères stricts. Ce résultat vide est valide et explicable, pas une erreur de génération.

Statut de l'itération: **PASS**.
