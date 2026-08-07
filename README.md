# V18.2.1 — Completeness First

Ce dépôt exécute le pipeline V18.2.1 d'enrichissement des univers Actions PEA et ETF PEA.

## Statut

V18.2.1 reste en **préproduction** jusqu'à la réussite des cycles d'audit correctifs et du run complet de confirmation sur le commit final. Aucun changement de la branche d'audit ne doit être fusionné dans `main` avant cette validation finale.

## Sources opérationnelles et testées

Les chemins suivants disposent d'un connecteur réel, de tests de régression et/ou d'un contrôle GitHub Actions utilisant le service externe :

- Yahoo Finance / yfinance : OHLCV bulk, fondamentaux et données de consensus/ETF lorsqu'elles sont exposées ;
- OpenFIGI v3 : résolution ISIN + MIC et réparation sécurisée des symboles ;
- Marketstack v2 : résolution `nom + MIC` puis fallback EOD avec contrôle strict de la place ;
- Alpha Vantage : fallback OHLCV global `TIME_SERIES_DAILY` uniquement, après Yahoo/OpenFIGI et Marketstack ;
- Finnhub : consensus analystes en complément, avec résolution de symbole contrôlée ;
- FRED : contexte macro officiel `VIXCLS` et `T10Y2Y` ;
- EIA v2 : contexte énergie officiel WTI et Brent ;
- MarketBeat via Parse : cross-check **sélectif au niveau émetteur** des objectifs et du consensus, avec séparation stricte entre cotation locale PEA et éventuel proxy ADR/US.

MarketBeat n'est jamais utilisé comme source de cours local PEA. Les données d'objectif d'un ADR restent dans leur devise propre et servent uniquement au suivi de dynamique analyste.

Alpha Vantage n'est volontairement **pas utilisé pour les fondamentaux européens** : l'audit a conservé le contrôle d'identité strict et validé le chemin global OHLCV à la place.

Sources encore prévues mais non configurées comme connecteurs actifs : Boursorama, Zonebourse, ABC Bourse, ingestion automatique Issuer/KID/AMF/Euronext, Morningstar et GDELT.

## Chaîne OHLCV

1. Yahoo Finance / yfinance ;
2. réparation éventuelle par OpenFIGI puis nouvelle tentative Yahoo ;
3. Marketstack v2 sur les échecs persistants ;
4. Alpha Vantage `TIME_SERIES_DAILY` sur au plus un échec prioritaire par run ;
5. normalisation en format commun puis calcul des indicateurs.

Les budgets Marketstack et Alpha Vantage sont **globaux au run Actions + ETF**, afin d'éviter de doubler involontairement la consommation API.

## Consensus, scénarios et Comité

Après le pipeline de collecte :

1. `scenario_fallback` garantit une couverture de scénarios sur les lignes `COMMITTEE/WATCH` ou, à défaut, sur le Top-300 par `score_brut` ;
2. `analyst_momentum` calcule les variations d'objectifs/consensus à source constante et les gates du Comité ;
3. `marketbeat_overlay` effectue un cross-check sélectif sur les émetteurs les mieux couverts par les analystes, avec pondération limitée et contrôle ADR/local ;
4. `committee_finalize` complète les champs de restitution, notamment la variation d'objectif à 12 mois en valeur absolue.

L'exécution demeure `SHADOW_BLOCKED` : ces modules enrichissent la décision mais ne transmettent aucun ordre.

## Configuration d'accès

Les valeurs d'accès aux API doivent rester dans `Settings → Secrets and variables → Actions` et ne doivent jamais être écrites dans le dépôt. Les sept secrets API consommés sont : `OPENFIGI_API_KEY`, `MARKETSTACK_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `FINNHUB_API_KEY`, `FRED_API_KEY`, `EIA_API_KEY` et `MARKETBEAT_API_KEY`.

`GITHUB_TOKEN` est fourni automatiquement par GitHub Actions.

## Provenance et caches

Les masters enrichis peuvent être réutilisés entre les runs lorsqu'ils restent compatibles avec les univers de référence. La provenance est conservée champ par champ dans une colonne technique privée du CSV, non exportée dans les Excel et exclue des calculs de couverture.

Chaque observation d'un lot est comparée à la provenance du **champ concerné avant le lot** : la mise à jour d'une métadonnée de groupe (`macro_as_of`, `ta_as_of`, etc.) ne peut donc plus rendre artificiellement fraîche une autre ancienne valeur du même lot.

Les caches de symboles sont réutilisés uniquement lorsqu'ils restent cohérents avec l'identité courante. Les erreurs transitoires ne sont pas transformées en correspondances négatives permanentes. Le mapping MarketBeat exige une preuve de cotation locale non-US avant d'autoriser un proxy analyste US.

## Quality gates

Le pipeline bloque notamment sur :

- compte de lignes et unicité des ISIN ;
- couverture ticker ;
- régression de couverture ;
- succès OHLCV minimum ;
- comptabilité des sources ;
- quotas globaux Marketstack et Alpha Vantage ;
- santé OpenFIGI ;
- erreurs runtime FRED/EIA ;
- seuils de disponibilité fondamentaux et consensus.

Le full audit ajoute des invariants plus stricts : scénario Comité non vide, enrichissement MarketBeat réellement exploitable, absence de quarantaine proxy et absence du faux conflit FRED de masse identifié lors du premier cycle.

Chaque run produit notamment `V18.2_QUALITY_GATES.json`, `V18.2_COVERAGE_BEFORE_AFTER.json`, `V18.2_SOURCE_FALLBACK_METRICS.json`, `V18.2_SCENARIO_METRICS.json`, les métriques analystes, les contextes macro/énergie et `V18.2_RUN_REPORT.xlsx`.

## Sécurité d'exécution

Le workflow principal est `.github/workflows/V18.2_online.yml`. Les résultats sont proposés via Pull Request et ne sont pas écrits directement dans `main`. Les workflows d'audit préproduction utilisent uniquement des droits de lecture sur le dépôt, hors workflow de production contrôlé.
