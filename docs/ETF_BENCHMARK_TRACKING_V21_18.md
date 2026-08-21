# V21.18 — ETF benchmark / tracking readiness

## Objet

V21.18 traite le verrou de donnée `official_benchmark` et prépare le calcul futur de tracking difference / tracking error sans introduire de proxy implicite.

Le chantier est **data/shadow only** : aucun poids, seuil de sélection, score MT/CT/LT, règle Entry/Exit, ordre, holdout ou périmètre T1/T2 n'est modifié.

## Benchmark officiel

Le collecteur structurel hebdomadaire peut désormais produire `official_benchmark` à partir des pages/factsheets déjà interrogés pour TER/AUM, mais uniquement lorsque :

1. la source correspond à l'ISIN exact ;
2. le benchmark apparaît après un libellé explicite tel que `Benchmark`, `Benchmark Index`, `Reference Index`, `Underlying Index`, `Index tracked` ou `Indice de référence` ;
3. la valeur n'est ni générique ni manifestement invalide.

Interdictions :

- déduire l'indice depuis le nom de l'ETF ;
- déduire l'indice depuis la catégorie, la géographie ou les holdings ;
- transformer automatiquement le nom de l'indice en ticker de marché.

Le benchmark est une donnée structurelle persistée dans le state V21.15 existant, TTL 365 jours. Aucun nouveau cache ni cron n'est créé. La provenance/hachage et les règles de non-écrasement V21.15 restent applicables.

## Preuve réseau réelle du 21/08/2026

Le workflow de qualification temporaire a interrogé l'univers complet de **102 ETF** et a exécuté le cycle : collecte exact-ISIN → gouvernance temporelle/provenance → merge → écriture d'un state isolé → replay du state → audit tracking readiness.

Résultats :

- benchmark présent dans le master d'entrée : **1/102** ;
- observations benchmark brutes : **52** ;
- observations benchmark acceptées exact-ISIN : **52/52** ;
- ETF distincts avec benchmark explicite : **52/102**, soit **50,98 %** ;
- preuve A émetteur : **9** ;
- preuve B justETF exact-ISIN : **43** ;
- quarantaine au merge benchmark : **0** ;
- state isolé écrit : **52 lignes / 52 ISIN** ;
- replay du state : **52/52**, 0 rejet ;
- SHA-256 du state de preuve : `0cc9412e5fc341776f4f5286586b1df3d692c8833a9c1c5ba5f35357a027c910` ;
- `production_state_modified = false`.

Le collecteur a produit 22 échecs de collecte au sens large TER/AUM/benchmark. Ils ne sont pas transformés en données manquantes neutres et n'empêchent pas les 52 preuves benchmark validées d'être utilisées.

## Tracking error

V21.18 **n'active pas** le calcul de tracking error.

Un futur calcul devra disposer d'un mapping exact et vérifié :

`official_benchmark -> benchmark_price_symbol/provider/source/evidence/date`

Le fichier `config/ETF_BENCHMARK_PRICE_MAP_V21_18.csv` est volontairement fail-closed. Aucun ticker n'y est créé par heuristique.

Dans la preuve réelle :

- `official_benchmark` disponible : **52/102** ;
- ETF sans benchmark explicite : **50/102** ;
- mappings de série de prix benchmark vérifiés : **0** ;
- lignes vendor avec au moins une valeur de tracking error : **1** ;
- lignes prêtes au calcul de tracking error : **0** ;
- `tracking_error_computation_enabled = false` ;
- influence décisionnelle : **0.0**.

Les éventuelles colonnes existantes `tracking_error_1y_pct`, `tracking_error_3y_pct`, `tracking_error_5y_pct` restent du contexte vendeur tant qu'elles ne sont pas reproduites avec une série de benchmark vérifiée.

## Audit de readiness

`python -m v182.audit.etf_benchmark_tracking_v21_18`

produit :

- `outputs/audit/V21_18_ETF_BENCHMARK_TRACKING_READINESS.csv`
- `outputs/audit/V21_18_ETF_BENCHMARK_TRACKING_READINESS.json`

Statuts :

- `BENCHMARK_NAME_MISSING`
- `BENCHMARK_PRICE_MAPPING_BLOCKED`
- `BENCHMARK_PRICE_MAPPING_VERIFIED_RESEARCH_ONLY`

Même le dernier statut ne donne aucune influence décisionnelle ; il signifie seulement que la prochaine étape de recherche pourrait charger une série de prix vérifiée.

## Critères de prochaine étape

Avant d'activer un calcul de tracking error :

1. benchmark explicite et sourcé par ISIN ;
2. mapping de prix vérifié, exact, non ambigu ;
3. contrôle du type d'indice (price/net return/gross return) et de la devise ;
4. au moins 252 rendements synchronisés pour le premier diagnostic ;
5. comparaison PIT/OOS et vérification de la cohérence avec les valeurs vendeur ;
6. aucune influence de score avant promotion dédiée.

V21.18 ne crée donc pas artificiellement une couverture de tracking error : elle rend mesurable et auditable ce qui est prêt et ce qui reste bloqué.