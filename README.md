# V18.2 — Completeness First

Ce dépôt exécute le pipeline V18.2 d'enrichissement des univers Actions PEA et ETF PEA.

## Statut

V18.2 reste en préproduction. La branche `main` ne doit pas recevoir ces changements avant validation du run complet sur le commit final.

Sources opérationnelles et testées dans le code actuel :

- Yahoo Finance / yfinance : OHLCV, fondamentaux et données de consensus/ETF lorsqu'elles sont exposées ;
- OpenFIGI v3 : résolution ISIN + MIC et réparation sécurisée des symboles ;
- Marketstack v2 : résolution de symbole par nom + MIC puis fallback EOD avec contrôle de place ;
- Finnhub : consensus analystes en complément, avec cache et contrôle de confiance du symbole.

Sources de la stratégie cible mais non encore implémentées comme connecteurs actifs : Alpha Vantage, FRED, EIA, Boursorama, Zonebourse, ABC Bourse, AMF/Euronext, Morningstar et GDELT.

## Chaîne OHLCV opérationnelle

1. Yahoo Finance / yfinance ;
2. OpenFIGI v3 pour la résolution ISIN/MIC et la réparation de symbole ;
3. nouvelle tentative Yahoo avec un symbole OpenFIGI validé ;
4. Marketstack v2, avec résolution `nom + MIC` puis EOD sur la place attendue ;
5. normalisation commune puis calcul des indicateurs.

Un ticker n'est considéré réussi que si un nombre minimal de cours `Close` réellement observés est disponible.

## Configuration d'accès

Les valeurs d'accès aux API restent dans les variables protégées GitHub Actions et ne doivent jamais être écrites dans le dépôt. Le workflow actuel consomme uniquement les accès OpenFIGI, Marketstack et Finnhub. Les accès Alpha Vantage, FRED et EIA resteront inutilisés jusqu'à l'implémentation de leurs connecteurs.

## OpenFIGI

Le cache `config/V18.2_OPENFIGI_MASTER_MAP.csv` est réutilisé seulement si le ticker Yahoo et le MIC attendus correspondent encore à l'identité courante. Une erreur transitoire n'est jamais enregistrée comme résultat négatif persistant.

## Marketstack

Marketstack intervient uniquement après échec Yahoo puis tentative de réparation OpenFIGI. Limites par défaut :

- 3 requêtes EOD maximum par run ;
- 1 nouvelle résolution de symbole maximum par run ;
- cache des résolutions positives et négatives avec durée de validité ;
- rejet des correspondances ambiguës ou de mauvaise place de cotation.

`MARKETSTACK_MAX_SYMBOLS_PER_RUN` permet de modifier la limite EOD lorsque le quota réel du compte est connu.

## Provenance

Les masters enrichis peuvent être réutilisés entre les runs lorsqu'ils restent compatibles avec les univers de référence. La provenance est conservée champ par champ dans une colonne technique privée du CSV. Cette colonne n'est pas exportée dans les Excel et n'entre pas dans le calcul de couverture.

Une observation de niveau de preuve inférieur ne doit pas dégrader une valeur officielle de niveau supérieur.

## Audits produits

Chaque run produit notamment :

- `outputs/audit/V18.2_QUALITY_GATES.json`
- `outputs/audit/V18.2_COVERAGE_BEFORE_AFTER.json`
- `outputs/audit/V18.2_SOURCE_FALLBACK_METRICS.json`
- `outputs/V18.2_RUN_REPORT.xlsx`

## Sécurité d'exécution

Le workflow principal est `.github/workflows/V18.2_online.yml`. Les résultats sont proposés via Pull Request et ne sont pas écrits directement dans `main`. Un workflow vert ne vaut pas validation fonctionnelle des sources encore explicitement marquées comme non implémentées.
