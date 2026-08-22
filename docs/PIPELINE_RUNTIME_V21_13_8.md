# Pipeline runtime V21.13.8/9 — optimisation par seuils de facturation

Date : 22/08/2026

## Règle catalyst verrouillée

PREMARKET et POSTMARKET utilisent exactement le même périmètre Actions :

- 20 présélections TCT maximum ;
- 20 présélections Actions CT maximum ;
- union dédupliquée par ISIN ;
- plafond absolu de 40 titres ;
- aucun fallback vers l'univers Actions complet si le marqueur de présélection manque.

Aucun ETF, Gold, Crypto/ETP ou IPO n'entre dans ce périmètre catalyst.

## Optimisation V21.13.8 — PREMARKET

Le workflow autonome PREMARKET n'installe plus la totalité des dépendances du projet. Il utilise un profil minimal limité aux dépendances réellement nécessaires au catalyst : NumPy, pandas, requests et yfinance. Le code projet est importé directement via `PYTHONPATH=src`.

Les dépendances lourdes sans usage dans ce job (notamment Playwright, PyArrow, OpenPyXL, pypdf et la stack de reporting complète) ne sont plus installées lors du PREMARKET.

## Optimisation V21.13.9 — I/O GitHub quotidien et hebdomadaire

Les états persistants ne sont plus restaurés puis recompressés par une succession de caches indépendants lorsque leurs règles de persistance sont identiques.

### Quotidien

Les cinq états TCT/catalyst/Action CT/provenance sont regroupés dans un cache décisionnel unique :

- `state/TCT_V24_1_7_T1_STATE.json` ;
- `state/tct_context/` ;
- `state/action_ct/` ;
- `state/action_ct_v22_1/` ;
- `state/provenance/`.

Après amorçage du nouveau cache, le workflow passe donc de cinq restaurations + cinq sauvegardes d'état à une restauration + une sauvegarde.

### Hebdomadaire

Les mêmes cinq états décisionnels utilisent le même cache partagé avec le quotidien. Deux états hebdomadaires sont regroupés dans un second cache dédié :

- `state/sector_rotation_v2/` ;
- `state/etf_fund_flows/`.

Après amorçage, le workflow hebdomadaire passe de sept restaurations + sept sauvegardes d'état à deux restaurations + deux sauvegardes.

Le cache OHLCV reste volontairement séparé et n'est sauvegardé qu'en cas de succès du job afin de conserver sa protection anti-poisoning.

La première exécution du nouveau format conserve des fallbacks vers les anciens caches. Aucune continuité historique n'est sacrifiée lors de la migration.

Les artefacts quotidien et hebdomadaire utilisent `compression-level: 1` au lieu de la compression par défaut plus coûteuse en CPU. Le contenu produit et transmis est inchangé.

## Invariants de qualité

Ces optimisations ne changent :

- aucun univers actif ;
- aucune donnée collectée ;
- aucune formule ;
- aucun poids ;
- aucun seuil ;
- aucun gate ;
- aucune règle PIT ;
- aucun statut de promotion ;
- aucune étape de scoring ou de validation ;
- aucun fichier de restitution métier ;
- aucun ordre.

## Stratégie de coût

Le pilotage est centré sur les seuils de facturation GitHub par job. Les trois seuils prioritaires sont :

| Job | Baseline V21.13.7 | Seuil cible | Effet de facturation visé |
|---|---:|---:|---:|
| PREMARKET ciblé | 1,2 min | < 1,0 min | 2 -> 1 minute facturable |
| Quotidien + POSTMARKET | 10,1 min | < 10,0 min | 11 -> 10 minutes facturables |
| Hebdo + vendredi + POSTMARKET | 25,2 min | < 25,0 min | 26 -> 25 minutes facturables |

Si les trois seuils sont atteints en télémétrie réelle, le budget mensuel théorique passe de 347,8 à environ 304,3 minutes, soit environ 5 h 04 par mois. Le gain théorique est d'environ 43,5 minutes par mois sans réduire le périmètre décisionnel restant.

La consolidation des caches et la baisse de compression sont précisément destinées à franchir les seuils quotidien et hebdomadaire sans supprimer le moindre calcul. Les durées réelles GitHub restent l'autorité avant toute nouvelle estimation de facturation.

## Pistes suivantes sans dégradation décisionnelle

1. Éviter les doubles lectures/calculs d'historiques entre Action CT V22.0 et V22.1, en conservant les sorties de contrôle et de divergence.
2. Réutiliser dans POSTMARKET les snapshots marché déjà produits dans le run principal lorsqu'ils sont strictement contemporains et conformes PIT, avec fallback réseau si absence ou staleness.
3. Réduire le coût d'installation Python uniquement par une méthode qui conserve les versions contraintes et passe la matrice de validation complète.
4. Ne lancer les validations SHADOW intégrales croissantes qu'à la cadence utile lorsque leur exécution quotidienne n'apporte aucune information décisionnelle nouvelle ; conserver les contrôles fail-closed nécessaires à chaque run.
5. Optimiser la collecte GDELT uniquement après démonstration A/B que le nombre d'articles, les événements détectés et les scores sont identiques ou meilleurs. Aucun batching approximatif de sociétés ne doit être promu sans cette preuve.

## Mesure

Les durées GitHub Actions observées restent l'autorité. La promotion d'une optimisation de temps exige :

- comparaison avant/après sur plusieurs runs ;
- absence de régression des fichiers de sortie ;
- mêmes présélections TCT/CT ;
- mêmes critères, poids, seuils et décisions canoniques ;
- aucune hausse du taux d'erreurs source ou des lignes dégradées.
