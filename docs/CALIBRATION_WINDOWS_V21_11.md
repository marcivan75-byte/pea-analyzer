# V21.11 — Gouvernance des fenêtres de calibration et stress tests

Date de référence : 20/08/2026

## 1. Objet

La V21.11 remplace la notion de fenêtre principale limitée à environ 35 mois par une architecture historique à deux compartiments strictement séparés. Elle ne modifie aucun poids, seuil, score, règle d'entrée/sortie, holdout ou autorité de décision.

## 2. Base principale de calibration

La base principale commence au **01/01/2023**.

- Du 01/01/2023 au 31/12/2027 : fenêtre **expansive post-COVID** allant du 01/01/2023 à la date PIT du run.
- Au 20/08/2026 : cette fenêtre touche **44 mois calendaires** (janvier 2023 à août 2026).
- À partir du **01/01/2028** : passage automatique à une fenêtre **glissante de 60 mois**.
- Après activation du rolling 60 mois, la borne de début est exactement `date PIT - 60 mois`.
- Pondération de calibration : **1,0**.

Cette base peut servir à la recherche/calibration des critères, poids et seuils, sous réserve des protocoles PIT/OOS et holdout propres à chaque module.

## 3. Bibliothèque de stress indépendante

La période **01/01/2020–31/12/2022** est conservée durablement dans une bibliothèque de stress séparée.

Elle sert notamment à analyser :

- robustesse des stops et mécanismes de protection ;
- drawdowns et vitesse de récupération ;
- changements violents de régime ;
- chocs de volatilité ;
- gaps ;
- stress de liquidité ;
- défaillances ou faux signaux des systèmes de protection.

Sa pondération dans la calibration ordinaire est **0,0**.

## 4. Interdictions de gouvernance

Les observations 2020–2022 ne peuvent jamais entrer dans la base principale de calibration V21.11.

Les résultats de stress ne peuvent pas être utilisés pour :

- optimiser les poids ordinaires ;
- optimiser les seuils ordinaires ;
- retuner un modèle après observation du stress set ;
- sélectionner ex post la variante qui aurait le mieux résisté au COVID.

Un stress test peut en revanche :

- **rejeter** une configuration manifestement fragile ;
- déclencher une revue des protections ;
- documenter un risque de drawdown ou de changement de régime ;
- comparer des protections déjà pré-enregistrées sans les optimiser sur le stress set.

## 5. Séquence de validation cible

La chaîne de recherche devient :

`CALIBRATION PRINCIPALE 2023+ → WALK-FORWARD / OOS → HOLDOUT FINAL VERROUILLÉ → STRESS 2020–2022 → DÉCISION DE PROMOTION`

Le stress set est donc un **test de robustesse terminal**, pas un ensemble d'apprentissage.

Les protocoles OOS et holdouts déjà définis par module restent prioritaires et inchangés. Par exemple, Sector Rotation V2 conserve ses périodes OOS pré-enregistrées et son holdout final ; V21.11 ne les remplace pas.

## 6. Implémentation exécutable

Références :

- `config/CALIBRATION_WINDOWS_V21_11.json` : politique centrale ;
- `src/v182/backtest/calibration_windows.py` : résolution et séparation des fenêtres ;
- `tests/test_calibration_windows_v21_11.py` : tests fail-closed.

Le code fournit notamment :

- résolution de la fenêtre principale à une date PIT donnée ;
- calcul du nombre de mois calendaires touchés ;
- partition d'un DataFrame en `primary`, `stress`, `outside` ;
- rejet explicite d'une calibration principale contenant des lignes de stress ;
- rejet explicite des lignes futures ;
- validation que le stress possède un poids de calibration nul et ne chevauche jamais la base principale.

## 7. PIT / anti-look-ahead

Une date supérieure à la date PIT du run est exclue de la calibration principale. Les variables dynamiques doivent toujours avoir été observables au moment de la décision simulée. Les rendements futurs, MAE/MFE et autres outcomes postérieurs restent exclusivement des résultats de validation.

Aucune donnée manquante n'est imputée pour augmenter artificiellement la taille de l'échantillon.

## 8. Données historiques conservées

La collecte OHLCV peut conserver une profondeur supérieure à la fenêtre de calibration. Cette conservation est souhaitée : le stockage historique et l'échantillon utilisé pour calibrer sont deux notions différentes.

Ainsi :

- les données 2020–2022 restent disponibles pour stress tests ;
- les données antérieures peuvent rester archivées pour traçabilité ou études historiques spécifiques ;
- seule la fenêtre gouvernée V21.11 peut alimenter une nouvelle calibration ordinaire ;
- les preuves historiques de modèles gelés restent documentées, mais ne sont pas réinterprétées comme une nouvelle calibration V21.11.
