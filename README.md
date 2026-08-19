# PEA Analyzer — V21.8.1 production / Data Integrity V21.9

Le moteur conserve les collecteurs V18.2 comme socle technique, mais l'univers de référence et la gouvernance sont désormais ceux de la chaîne V21.x.

## Univers canoniques

- **Actions PEA : 1 829 ISIN exacts**, protégés par la whitelist `config/V21_3_ACTION_UNIVERSE_1829_ISINS.parts` et son SHA-256.
- **ETF PEA : 102 ISIN** dans `inputs/V18.2_PEA_ETF_MASTER.csv`.
- Les lignes Actions présentes dans la whitelist mais absentes du master historique sont matérialisées comme `WHITELIST_ONLY_MISSING_METADATA` : elles conservent l'identité canonique mais restent **BLOCK_DATA / non scorables** jusqu'à hydratation sourcée. Aucun ticker ni attribut n'est inventé.

## Séquence de collecte

1. `yfinance` bulk sur les historiques Actions.
2. `yfinance` bulk sur les historiques ETF.
3. Calcul local des indicateurs dérivables.
4. `yfinance` info sur les Actions.
5. Consensus Finnhub lorsque disponible.
6. Enrichissement ETF via sources publiques/émetteurs et données de marché.
7. Sources officielles ou attribuées pour validation et conflits.
8. Scénarios et asymétrie sur les short-lists.

Aucune clé API n'est stockée dans le dépôt. Les valeurs manquantes restent manquantes ; l'imputation neutre est interdite.

## Intégrité de la base maître

Le contrat `config/MASTER_DATA_CONTRACT_V21_9.json` distingue le snapshot courant des données PIT historiques.

Le CI exécute désormais :

- validation format + checksum des ISIN ;
- unicité des ISIN ;
- cohérence de l'identité et des statuts de validation ;
- provenance obligatoire pour les corrections ISIN et identités finales ;
- contrôle des dates futures ;
- contrôle de plausibilité des principales variables quantitatives ;
- profil de couverture des champs d'identité, qualitatifs, fondamentaux et marché ;
- tests complets Python/référentiels.

Les résultats sont publiés sous `outputs/audit/MASTER_DATA_AUDIT.json`, `MASTER_DATA_AUDIT_ISSUES.csv`, `MASTER_DATA_PROFILE.json` et `MASTER_DATA_FIELD_COVERAGE.csv` dans les artefacts CI.

## Règle backtests / anti-look-ahead

Le master courant est un **snapshot actuel**, pas une reconstruction historique. Les champs dynamiques du master courant ne peuvent donc jamais être utilisés comme vérité historique à une date simulée.

Pour un backtest, toute variable dynamique doit provenir d'une observation horodatée disponible au plus tard à l'instant de décision simulé. Les rendements futurs, MAE/MFE et labels postérieurs sont des résultats uniquement et ne peuvent pas devenir des features. Les holdouts gouvernés restent verrouillés jusqu'à décision explicite.

## Exécuter en ligne (GitHub Actions)

Secrets/variables possibles : `MARKETSTACK_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `FINNHUB_API_KEY`, `FRED_API_KEY`, `EIA_API_KEY`, `OPENFIGI_API_KEY`.

Les traitements utilisent les sources disponibles selon la politique de fallback du référentiel. OpenFIGI peut servir à la résolution d'identité/ticker ; un résultat non résolu reste en gap et n'est jamais remplacé par un ticker inventé.

## Sources et données propriétaires

- Les performances et données de marché observables sont calculées/récupérées via les sources autorisées configurées.
- `morningstar_rating` et les rangs de catégorie restent `NON_OBSERVE` lorsqu'aucune source attribuée/autorisée n'est disponible ; ils ne sont pas devinés.
- Les conflits de données sont mis en quarantaine et ne sont remplacés automatiquement que par une observation de meilleure preuve, ou plus fraîche à niveau de preuve égal.
- Les contrôles manuels officiels passent par des worklists et des overrides sourcés, jamais par une valeur libre non attribuée.

## Exécuter en local

```bash
pip install -e .[test]
python -m v182.audit.master_data --root . --fail-fatal
python -m v182.audit.master_data_profile
pytest -q
```

## Gouvernance

- Publication des changements par pull request ; pas de modification directe de `main` par les runs automatiques.
- Les anomalies **FATAL** bloquent la validation.
- Les anomalies **BLOCK_DATA** mettent la donnée concernée hors influence jusqu'à correction sourcée.
- Aucune correction factuelle (ISIN, ticker, identité, qualitatif) n'est inventée ; seule une normalisation déterministe de format peut être automatisée sans source externe.
