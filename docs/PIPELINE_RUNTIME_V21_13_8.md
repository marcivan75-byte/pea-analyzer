# Pipeline runtime V21.13.8 — optimisation par seuils de facturation

Date : 22/08/2026

## Règle catalyst verrouillée

PREMARKET et POSTMARKET utilisent exactement le même périmètre Actions :

- 20 présélections TCT maximum ;
- 20 présélections Actions CT maximum ;
- union dédupliquée par ISIN ;
- plafond absolu de 40 titres ;
- aucun fallback vers l'univers Actions complet si le marqueur de présélection manque.

Aucun ETF, Gold, Crypto/ETP ou IPO n'entre dans ce périmètre catalyst.

## Optimisation immédiate V21.13.8

Le workflow autonome PREMARKET n'installe plus la totalité des dépendances du projet. Il utilise un profil minimal limité aux dépendances réellement nécessaires au catalyst : NumPy, pandas, requests et yfinance. Le code projet est importé directement via `PYTHONPATH=src`.

Cette modification ne change :

- aucun univers ;
- aucune donnée collectée ;
- aucune formule ;
- aucun poids ;
- aucun seuil ;
- aucun gate ;
- aucune règle PIT ;
- aucun statut de promotion ;
- aucun ordre.

Les dépendances lourdes sans usage dans ce job (notamment Playwright, PyArrow, OpenPyXL, pypdf et la stack de reporting complète) ne sont plus installées lors du PREMARKET.

## Stratégie de coût

Le pilotage est désormais centré sur les seuils de facturation GitHub par job. Les trois seuils prioritaires sont :

| Job | Baseline V21.13.7 | Seuil cible V21.13.8 | Effet de facturation visé |
|---|---:|---:|---:|
| PREMARKET ciblé | 1,2 min | < 1,0 min | 2 -> 1 minute facturable |
| Quotidien + POSTMARKET | 10,1 min | < 10,0 min | 11 -> 10 minutes facturables |
| Hebdo + vendredi + POSTMARKET | 25,2 min | < 25,0 min | 26 -> 25 minutes facturables |

Si les trois seuils sont atteints en télémétrie réelle, le budget mensuel théorique passe de 347,8 à environ 304,3 minutes, soit environ 5 h 04 par mois. Le gain théorique est d'environ 43,5 minutes par mois sans réduire le périmètre décisionnel restant.

Le premier objectif opérationnel est le passage du PREMARKET sous 1 minute. Les optimisations quotidiennes et hebdomadaires ne doivent être promues qu'après mesure des durées de chaque bloc et sans supprimer de donnée utile au TCT/CT ou au CI.

## Pistes suivantes sans dégradation décisionnelle

1. Éviter les doubles lectures/calculs d'historiques entre Action CT V22.0 et V22.1, en conservant les sorties de contrôle et de divergence.
2. Réutiliser dans POSTMARKET les snapshots marché déjà produits dans le run principal lorsqu'ils sont strictement contemporains et conformes PIT, avec fallback réseau si absence ou staleness.
3. Ne lancer les validations SHADOW intégrales croissantes qu'à la cadence utile lorsque leur exécution quotidienne n'apporte aucune information décisionnelle nouvelle ; conserver les contrôles fail-closed nécessaires à chaque run.
4. Optimiser la collecte GDELT uniquement après démonstration A/B que le nombre d'articles, les événements détectés et les scores sont identiques ou meilleurs. Aucun batching approximatif de sociétés ne doit être promu sans cette preuve.

## Mesure

Les durées GitHub Actions observées restent l'autorité. La promotion d'une optimisation de temps exige :

- comparaison avant/après sur plusieurs runs ;
- absence de régression des fichiers de sortie ;
- mêmes présélections TCT/CT ;
- mêmes critères, poids, seuils et décisions canoniques ;
- aucune hausse du taux d'erreurs source ou des lignes dégradées.
