# PEA Analyzer — V21.8.1 production / Data Integrity V21.9.1

Le moteur conserve les collecteurs V18.2 comme socle technique, mais l'univers de référence et la gouvernance des données sont désormais ceux de la chaîne V21.x. La V21.9.1 durcit l'identité, la provenance, les contrôles de domaine, la sécurité des sources et la mesure de couverture sans modifier les pondérations de décision.

## Univers canoniques

- **Actions PEA : 1 829 ISIN exacts**, protégés par la whitelist `config/V21_3_ACTION_UNIVERSE_1829_ISINS.parts` et son SHA-256.
- **ETF PEA : 102 ISIN** dans `inputs/V18.2_PEA_ETF_MASTER.csv`.
- L'overlay gouverné `config/V21_9_ACTION_IDENTITY_MAP.parts` couvre les 399 Actions qui étaient absentes du master historique : **360 disposent désormais d'un ticker EEE validé** et **39 restent identifiées mais BLOCK_DATA / non scorables** faute de ticker EEE suffisamment prouvé.
- Couverture identité Actions gouvernée : ISIN 100 %, nom 100 %, ticker exploitable 97,87 % ; soit **1 790 Actions éligibles aux données de marché** sur 1 829.
- Les 102 ETF conservent une identité exploitable complète. Aucun ISIN, ticker ou attribut factuel n'est inventé pour combler une lacune.

## Séquence de collecte

1. `yfinance` bulk sur les historiques Actions.
2. `yfinance` bulk sur les historiques ETF.
3. Calcul local des indicateurs dérivables.
4. `yfinance` info sur les Actions.
5. Consensus Finnhub lorsque l'accès et les droits de la source le permettent.
6. Enrichissement ETF via données Yahoo brutes et sources publiques/émetteurs.
7. Sources officielles ou attribuées pour validation et conflits.
8. Scénarios et asymétrie sur les short-lists.

Aucune clé API n'est stockée dans le dépôt. Les valeurs manquantes restent manquantes ; l'imputation neutre est interdite.

## Intégrité de la base maître

Le contrat `config/MASTER_DATA_CONTRACT_V21_9.json`, version logique **V21.9.1**, distingue le snapshot courant des données PIT historiques et impose une politique fail-closed.

Le CI exécute notamment :

- validation format + checksum des ISIN ;
- unicité des ISIN ;
- cohérence de l'identité et des statuts de validation ;
- validation déterministe de l'overlay d'identité Actions ;
- provenance obligatoire pour les corrections ISIN et identités finales ;
- contrôle des dates futures ;
- contrôle de plausibilité des principales variables quantitatives ;
- mise en quarantaine avant fusion de toute valeur quantitative hors domaine ;
- profil de couverture sémantique des champs d'identité, qualitatifs, fondamentaux et marché ;
- tests complets Python/référentiels.

Les résultats sont publiés sous `outputs/audit/MASTER_DATA_AUDIT.json`, `MASTER_DATA_AUDIT_ISSUES.csv`, `MASTER_DATA_PROFILE.json` et `MASTER_DATA_FIELD_COVERAGE.csv` dans les artefacts CI.

## Politique Yahoo ETF : TER, actifs et AUM

Les champs Yahoo sont conservés sous des noms bruts stables et avec provenance. Ils ne deviennent des critères canoniques que lorsque leur unité est suffisamment prouvée.

- `annualReportExpenseRatio` peut alimenter `ter_pct` uniquement s'il s'agit d'un ratio décimal fini compris entre 0 et 1 ; la conversion appliquée est alors `ratio × 100`.
- `totalAssets` est conservé comme **donnée brute** `total_assets_yf` lorsqu'il est disponible.
- La devise Yahoo `currency` est la **devise de cotation**. Elle ne prouve pas la devise de `totalAssets` et ne peut donc jamais servir seule à fabriquer `aum_m` ou `fund_total_assets_eur_m`.
- Un AUM en EUR ne peut être dérivé que si une devise propre aux actifs du fonds est explicitement fournie et attribuée.

Le run réel V21.9.1 sur les 102 ETF a observé `totalAssets` brut sur 83,33 % des ETF et la devise de cotation sur 99,02 %, mais a créé **0 nouvelle observation AUM EUR**. Le TER demeure à 10,78 % de couverture dans le master, car Yahoo n'a fourni aucun `annualReportExpenseRatio` exploitable lors du run de validation. L'absence de preuve n'est pas transformée en couverture artificielle.

## Sécurité des sources et diagnostics

- Les clés et tokens API sont interdits dans les logs et artefacts.
- Les paramètres secrets de requête et les Bearer tokens sont masqués avant journalisation.
- Un refus d'authentification ou d'entitlement 401/403 déclenche un **arrêt rapide de la source** au lieu de milliers de requêtes garanties en échec.
- Un cache valide peut rester utilisable uniquement dans sa durée de validité originale et avec son horodatage d'origine ; un échec de source n'étend jamais artificiellement sa fraîcheur.

## Règle backtests / anti-look-ahead

Le master courant est un **snapshot actuel**, pas une reconstruction historique. Les champs dynamiques du master courant ne peuvent donc jamais être utilisés comme vérité historique à une date simulée.

Pour un backtest, toute variable dynamique doit provenir d'une observation horodatée disponible au plus tard à l'instant de décision simulé. Les rendements futurs, MAE/MFE et labels postérieurs sont des résultats uniquement et ne peuvent pas devenir des features. Les holdouts gouvernés restent verrouillés jusqu'à décision explicite.

## Exécuter en ligne (GitHub Actions)

Secrets/variables possibles : `MARKETSTACK_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `FINNHUB_API_KEY`, `FRED_API_KEY`, `EIA_API_KEY`, `OPENFIGI_API_KEY`.

Les traitements utilisent les sources disponibles selon la politique de fallback du référentiel. OpenFIGI peut servir à la résolution d'identité/ticker ; un résultat non résolu reste en gap et n'est jamais remplacé par un ticker inventé. Une source refusée pour authentification ou entitlement ne doit pas dégrader la sécurité des logs ni provoquer une boucle de requêtes inutiles.

## Sources et données propriétaires

- Les performances et données de marché observables sont calculées/récupérées via les sources autorisées configurées.
- `morningstar_rating` et les rangs de catégorie restent `NON_OBSERVE` lorsqu'aucune source attribuée/autorisée n'est disponible ; ils ne sont pas devinés.
- Les conflits de données sont mis en quarantaine et ne sont remplacés automatiquement que par une observation de meilleure preuve, ou plus fraîche à niveau de preuve égal.
- Les contrôles manuels officiels passent par des worklists et des overrides sourcés, jamais par une valeur libre non attribuée.
- Les alias sémantiques du profil de couverture (`sector_yf` → secteur, `target_mean_yf` → target price, etc.) servent uniquement au **reporting de couverture** : ils ne recopient pas les valeurs et ne changent aucun score.

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
- Une amélioration de couverture n'est acceptée que si l'unité, la provenance et la sémantique du champ sont suffisamment prouvées ; une couverture plus faible mais exacte est préférée à une couverture artificiellement gonflée.
