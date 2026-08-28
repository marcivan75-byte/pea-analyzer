# Optimisation entière du process HEBDO V4

Date : 2026-08-28  
Package analysé : `HEBDO_V4_TRES_COMPLET_AUDITE_OPTIMISE_FINAL_2026-08-27.zip`  
Référence : `3b52686f56ed68c63e4a057509c880ea0c3217ff`

## Déjà certifié

Baseline V4.3 : 1019,423 s sous cible 1200 s. Tests 930 passed. Aucun ordre réel.
Optimisations 27/08 conservées : pas de second CI LIGHT, pas de recomput amont, ZIP en flux.

## Invariants

CI et CI LIGHT indépendants. Sources externes ne créent jamais un candidat CI. Fail-closed.
Objectif/Risque/Sector/Fund Flows/TCT V24.4.x = SHADOW, influence 0. T1/T2 Actions TCT only. WIP=1.

## Ce qui change en V4.4

Le Friday GitHub n'appelait pas le runner opérationnel local. Il forçait LIVE et restaurait 9 caches.

V4.4 unifie :
- `python -m v182.reporting.weekly_operational_runner_v4_4`
- `CACHE_PREFERRED` par défaut ; `LIVE` seulement si `maintenance_full_refresh`
- `PEA_WEEKLY_CRITICAL_ONLY=1`
- timeout 45 min
- 3 caches consolidés seulement
- répertoire yfinance writable avant collecte

Aucun critère, poids, seuil, univers ou PIT n'est modifié.
