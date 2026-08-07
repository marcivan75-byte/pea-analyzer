# V18.2 — Completeness First

Ce dépôt exécute le pipeline V18.2 d'enrichissement des univers Actions PEA et ETF PEA.

## Exécution

Le workflow GitHub Actions principal est `.github/workflows/V18.2_online.yml`.

Chaîne OHLCV opérationnelle :

1. Yahoo Finance / yfinance — source bulk primaire.
2. OpenFIGI v3 — résolution ISIN → FIGI / ticker / place / MIC et réparation des symboles Yahoo qui ne produisent pas de données exploitables.
3. Yahoo Finance — nouvelle tentative uniquement avec les symboles réparés par OpenFIGI.
4. Marketstack v2 EOD — repli sur les échecs persistants, avec quota conservateur par défaut.
5. Les données récupérées par les sources de repli sont normalisées dans le même format MultiIndex OHLCV avant le calcul des indicateurs.

Le pipeline ne considère plus qu'un ticker Yahoo est réussi parce que ses colonnes existent : il exige désormais un nombre minimal de cours `Close` réellement observés.

## Secrets GitHub Actions

Les clés doivent être stockées dans `Settings → Secrets and variables → Actions` et ne doivent jamais être inscrites dans le dépôt.

Variables attendues :

- `MARKETSTACK_API_KEY`
- `OPENFIGI_API_KEY`
- `ALPHA_VANTAGE_API_KEY`
- `FINNHUB_API_KEY`
- `FRED_API_KEY`
- `EIA_API_KEY`

`GITHUB_TOKEN` est fourni automatiquement par GitHub Actions.

## OpenFIGI

Le pipeline utilise exclusivement l'API OpenFIGI **v3**. Un cache persistant est construit dans `config/V18.2_OPENFIGI_MASTER_MAP.csv`. Les ISIN déjà résolus ne sont pas redemandés lors des runs suivants.

Le cache conserve notamment :

- ISIN ;
- ticker OpenFIGI ;
- code de place ;
- MIC ;
- FIGI / composite FIGI / share-class FIGI ;
- candidat de ticker Yahoo.

## Marketstack

Marketstack est utilisé uniquement après échec de Yahoo puis tentative de réparation OpenFIGI. Le nombre de symboles Marketstack est plafonné afin d'éviter de consommer involontairement un forfait API.

Valeur par défaut : `4` symboles maximum par run.

Cette limite peut être augmentée en définissant l'environnement `MARKETSTACK_MAX_SYMBOLS_PER_RUN` lorsque le quota réel du compte Marketstack est connu.

La configuration par défaut demande 365 jours d'historique EOD, compatible avec les capacités usuelles du plan gratuit. Les comptes supérieurs peuvent augmenter `marketstack.history_days` dans `config/V18.2_MASTER_CONFIG.json`.

## Audits produits

Chaque run produit notamment :

- `outputs/audit/V18.2_QUALITY_GATES.json`
- `outputs/audit/V18.2_COVERAGE_BEFORE_AFTER.json`
- `outputs/audit/V18.2_SOURCE_FALLBACK_METRICS.json`
- `outputs/V18.2_RUN_REPORT.xlsx`

`V18.2_SOURCE_FALLBACK_METRICS.json` permet de distinguer les récupérations Yahoo, les réparations de symboles via OpenFIGI et les récupérations Marketstack.

## Sécurité d'exécution

Le pipeline reste en mode contrôlé : les fichiers produits sont publiés dans une Pull Request et ne sont pas écrits directement dans `main`. Les quality gates doivent être verts avant fusion.
