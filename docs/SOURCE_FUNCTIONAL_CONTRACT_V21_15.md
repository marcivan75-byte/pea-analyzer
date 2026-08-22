# V21.15.0 — Contrat fonctionnel des sources sélectionnées

## Pourquoi cette version existe

Une régression fonctionnelle a été constatée : une version antérieure du processus utilisait Boursorama comme source prioritaire après présélection et Investing.com comme confirmation technique multi-horizon, mais ces fonctions n'étaient plus garanties dans le code courant.

V21.15 restaure ces fonctions et les transforme en invariants testés par CI afin qu'une optimisation future de temps, de cache ou de fournisseurs ne puisse plus les supprimer silencieusement.

## Architecture verrouillée : score d'abord, enrichissement ensuite

1. Les moteurs internes TCT, CT et MT calculent leurs scores et leurs présélections avec leurs référentiels gouvernés.
2. Seuls les titres déjà présélectionnés sont transmis à la couche `selected_source_enrichment`.
3. Boursorama enrichit prioritairement les Actions retenues.
4. Investing.com fournit la synthèse technique publique Journalier / Hebdomadaire / Mensuel pour les Actions et ETF retenus lorsqu'une URL publique est résolue et validée.
5. Ces champs sont joints aux lignes de décision/ranking pour la restitution et la confirmation qualitative.
6. Ils ne modifient ni les poids, ni les seuils, ni les scores de référence et ne peuvent pas créer seuls un `BUY_CANDIDATE`.

## Boursorama : fiche prioritaire des Actions présélectionnées

Pour les Actions TCT / CT / MT retenues, le collecteur sélectif récupère selon disponibilité publique :

- cours d'ouverture, clôture précédente, haut/bas, volume ;
- capitalisation/valorisation ;
- secteur et éligibilité PEA affichée ;
- consensus FactSet, nombre d'analystes, objectif médian, potentiel et révisions ;
- PER et rendement estimés ;
- estimations BPA, dividende, rendement et PER 2026/2027 ;
- chiffre d'affaires, résultat net, marge opérationnelle, ROE et dette financière ;
- risque ESG affiché lorsqu'il est présent.

Deux TTL sont séparés :

- dynamique : 8 h ;
- fiche profonde/chiffres clés : 168 h.

Le HTML brut n'est jamais conservé. Seuls les champs normalisés, URL, horodatages et hash de page sont persistés.

## Investing.com : synthèse technique multi-horizon

La page publique de technique expose cinq états normalisés :

- `STRONG_SELL`
- `SELL`
- `NEUTRAL`
- `BUY`
- `STRONG_BUY`

Les trois horizons restaurés sont :

- `TCT` <- signal `DAILY` ;
- `CT` <- signal `WEEKLY` ;
- `MT` <- signal `MONTHLY`.

Les trois verdicts Daily / Weekly / Monthly restent également visibles simultanément afin de détecter les divergences de tendance.

Le résolveur Investing est sélectif. Il privilégie une URL déjà validée/cachée, sinon tente au maximum quelques slugs publics dérivés du nom/ticker et ne conserve un mapping que si la fiche générale contient exactement l'ISIN attendu. Aucun résultat ambigu n'est accepté.

## Optimisation du temps

La couche est limitée à 40 instruments uniques au maximum par run.

Boursorama et Investing sont lancés en parallèle car ce sont deux fournisseurs distincts. Chaque fournisseur conserve son propre limiteur de départ de requêtes et son cache. Une défaillance externe produit un `N/A` audité et ne supprime pas la décision interne déjà calculée.

## Intégrations verrouillées

La CI vérifie explicitement la présence de `enrich_selected_rows` dans :

- `daily_tct_ct_runner.py` — TCT / CT ;
- `action_mt_shadow_run_v1.py` — Action MT ;
- `etf_mt_v2081_run.py` — ETF MT.

La configuration de référence est `config/SOURCE_FUNCTIONAL_CONTRACT_V21_15.json`.

Toute suppression silencieuse de ces hooks ou modification des cinq états/horizons doit faire échouer les tests.
