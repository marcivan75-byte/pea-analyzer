# V18.2 — Completeness First

V18.2 conserve la doctrine et les moteurs de la V18.1, mais change la priorité :
la première mission est de compléter réellement les deux référentiels.

## Séquence

1. `yfinance` bulk sur les historiques Actions.
2. `yfinance` bulk sur les historiques ETF.
3. Calcul local de tous les indicateurs dérivables.
4. `yfinance` info sur les Actions prioritaires.
5. Consensus Boursorama/Zonebourse en bulk.
6. ETF ABC Bourse/Boursorama/émetteurs.
7. Sources officielles pour validation et conflits.
8. Scénarios et asymétrie sur les short-lists.

Aucune nouvelle demande systématique des clés déjà transmises.
Aucune clé n'est stockée dans le package.

## Exécuter en ligne (GitHub Actions)

1. Pousser ce dossier comme repo GitHub.
2. Settings → Secrets and variables → Actions → ajouter (celles que vous avez) :
   `MARKETSTACK_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `FINNHUB_API_KEY`, `FRED_API_KEY`, `EIA_API_KEY`,
   `OPENFIGI_API_KEY` (optionnelle, augmente le débit de résolution des tickers ETF).
   Seule `FINNHUB_API_KEY` est nécessaire pour la Wave 05 ; les Waves 00-04, 06 et 08 fonctionnent
   sans aucune clé (yfinance, OpenFIGI en mode gratuit, calculs internes).
3. Onglet Actions → "V18.2 Completeness First" → "Run workflow" (ou attendre le cron du lundi au vendredi).
4. Le run pousse dans `outputs/` : les référentiels enrichis, le rapport de couverture avant/après,
   et dans `outputs/gaps/` les lignes qui restent `INPUT_REQUIRED` (ex : ETF sans ticker mappé,
   conflits non résolus).

## Points d'attention avant le premier run

- **ETF sans `yahoo_ticker`** : résolu automatiquement en Wave 00 via l'API
  OpenFIGI (gratuite, ISIN -> ticker par place de cotation), voir
  `src/v182/mapping/etf_isin_resolver.py`. Aucune clé requise pour démarrer ;
  définir `OPENFIGI_API_KEY` en secret GitHub augmente le débit autorisé.
  Les ISIN non résolus (place de cotation non couverte par la table de
  correspondance interne) partent dans
  `outputs/gaps/V18.2_ETF_TICKER_OPENFIGI_GAPS.csv` — jamais de ticker inventé.
  On peut aussi compléter `config/V18.2_ETF_TICKER_MAP.csv` à la main.
- **Waves 05/06 (consensus & ETF)** : le scraping Boursorama/Zonebourse/ABC Bourse prévu à l'origine
  a été remplacé par des sources plus fiables, faute d'avoir pu valider la structure HTML réelle de
  ces sites (pages rendues dynamiquement, sélecteurs CSS non vérifiables sans navigateur réel) :
  - **Wave 05** (consensus Actions hors champs `*_yf`) : API Finnhub (`FINNHUB_API_KEY`), gratuite,
    60 req/min. Alimente `consensus`, `consensus_rating`, `consensus_score`, `buy_n`, `hold_n`,
    `sell_n`, `n_analysts`, `target_price`, `consensus_period`.
  - **Wave 06** (ETF) : la majorité des colonnes ETF manquantes (`perf_1y/3y/5y_pct`) sont en fait
    couvertes par la **Wave 03** dès que la Wave 00 a résolu le ticker Yahoo de l'ETF — pas besoin de
    scraping. Seul `dividend_yield_pct`/`dividend_data_status` est complété ici via yfinance.
  - **Gap connu, sans solution gratuite identifiée** : `morningstar_rating` et `rank_cat_1y/3y/5y`
    sont des métriques propriétaires Morningstar. Elles restent `NON_OBSERVE` plutôt que d'être
    scrapées de façon non fiable ou devinées. Si vous avez un accès Morningstar Direct/Office, c'est
    la seule voie fiable pour ces colonnes.
  - Le squelette de scraping générique (`waves.wave_public_table`, piloté par
    `config/V18.2_SCRAPE_SELECTORS.json`) reste disponible en repli optionnel si vous voulez
    tenter Boursorama/ABC Bourse malgré tout — à vos risques, sélecteurs non validés.
- **Wave 08 (scénarios)** : formule interne simplifiée basée sur l'ATR14, à valider/affiner —
  ce n'est pas une prédiction, seulement un remplissage cohérent à partir de la volatilité
  observée (voir docstring de `wave8_scenarios`).
- **Wave 07** : ne résout un conflit en quarantaine que si une valeur validée est fournie dans
  `config/V18.2_MANUAL_OVERRIDES.csv` — jamais de valeur inventée. Aucune API officielle gratuite
  et automatisable n'existe (Euronext Reference Data et l'AMF GECO sont consultables manuellement
  mais pas via API publique) : Wave 07 génère donc en plus une **check-list humaine**
  (`outputs/gaps/V18.2_WAVE07_WORKLIST.csv`) avec, pour chaque conflit non résolu et chaque gap
  critique PEA (`pea_confidence`, `broker_pea_confirmed`, `corporate_status`), un lien direct vers
  la fiche Euronext officielle (réutilise `euronext_link`, déjà présent pour 97% des Actions) et
  un lien vers la base GECO de l'AMF. Une fois vérifié à la main, reporter la valeur dans
  `V18.2_MANUAL_OVERRIDES.csv` pour qu'elle soit appliquée avec evidence A au run suivant.

## Exécuter en local

```
pip install -e .[test]
pytest -q
python -m v182.reporting.run
```


## Release corrigée opérationnelle

- Référentiels intégrés : 1 486 actions avec tickers consolidés et 102 ETF avec identité validée.
- Checkpoints isolés par identifiant de run.
- Seuils bloquants : 100 % tickers, absence de régression, au moins 90 % de succès OHLCV.
- Les résultats sont publiés comme artefacts Excel et via pull request, jamais poussés directement sur la branche principale.
- `OPENFIGI_API_KEY` est transmis par secret GitHub, sans être stocké dans le dépôt.
