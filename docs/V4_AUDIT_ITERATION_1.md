# V4 Hebdo — audit 1/5 — référentiels et invariants

Date: 2026-08-24

## Périmètre

- Référence immuable: commit `3b52686f56ed68c63e4a057509c880ea0c3217ff`.
- 633 critères Actions et 268 critères ETF.
- Vecteurs Actions CT, MT, SHORT et TOP_DOWN.
- Vecteurs ETF CT, SHORT, TOP_DOWN, MT baseline et noyau dynamique MT de 38 critères.
- Pondération de confiance V22.2.
- Contrats de sources et règles de sécurité.

## Constats

1. Les comptes de critères déclarés concordent entre les registres et l'intégrité globale.
2. Tous les vecteurs actifs sont finis, non négatifs et normalisés à 100 %.
3. Chaque critère pondéré possède une direction déclarée.
4. Les pondérations de confiance totalisent 100 %.
5. Aucun jeu historique PIT/OOS livré avec la référence ne permet une nouvelle optimisation honnête. Les poids de référence restent donc actifs; toute repondération automatique est interdite.
6. Le test de chemin d'état était dépendant de Linux et échouait sous Windows.
7. Le parseur de date ETF émettait un avertissement ambigu en appliquant `dayfirst=True` à une date ISO.
8. Le contrat V22 mentionnait encore Investing alors que sa résolution n'était pas assez factuelle. V4 le remplace par TradingView avec identité exacte et échec fermé.
9. Les ETF ne doivent pas hériter d'un contrat de consensus analystes conçu pour les actions.

## Corrections

- Ajout d'un référentiel V4 unique et d'un contrat de sources V4.
- Ajout d'un audit exécutable à 68 invariants fatals.
- Correction du test de chemin avec `Path.as_posix()`.
- Parsing explicite des dates ISO ETF.
- Gel explicite des poids tant qu'une preuve PIT/OOS reproductible n'existe pas.
- Séparation des règles Boursorama Actions et ETF.

## Validation

- Audit de gouvernance: **68/68 PASS**.
- Tests ciblés: **25 PASS**.
- Baseline complète avant correction: **863 PASS, 1 échec de portabilité**.
- Ruff fonctionnel: **PASS**.
- Compilation Python: **PASS**.

Statut de l'itération: **PASS**.
