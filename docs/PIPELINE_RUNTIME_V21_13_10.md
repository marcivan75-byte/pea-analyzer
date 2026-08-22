# Pipeline runtime V21.13.10 — historique Actions CT partagé sans perte

Date : 22/08/2026

## Objectif

Réduire le temps du run quotidien et du run du vendredi sans diminuer la qualité des données, des critères, des scores, des validations PIT ou des restitutions CI.

## Changement

Les moteurs Actions CT V22.0 et V22.1 continuaient jusqu'ici à lire séparément le même cache Parquet `data/cache/actions` dans deux processus Python successifs.

V21.13.10 :

- exécute V22.0 puis V22.1 dans un orchestrateur Python unique ;
- conserve strictement cet ordre, car V22.1 compare toujours son résultat au parent V22.0 ;
- utilise le loader historique gouverné existant pour la première lecture physique ;
- réutilise ensuite en mémoire le même lot brut pour V22.1 ;
- fournit une copie profonde indépendante à chaque moteur afin d'interdire toute contamination croisée ;
- si V22.1 demande un univers plus large que V22.0, le loader relit explicitement l'union des tickers au lieu de retourner des données incomplètes ;
- si V22.0 lève une exception, V22.1 est malgré tout exécuté, comme avec les deux anciennes étapes GitHub `continue-on-error` ;
- restaure le loader original après le bundle, y compris en cas d'exception.

## Invariants verrouillés

Aucun changement sur :

- les données financières ;
- l'univers Actions CT ;
- les critères ;
- les poids ;
- les seuils ;
- les formules V22.0 et V22.1 ;
- les règles daily/weekly ;
- le garde de clôture 18 h Europe/Paris ;
- les états temporels de sortie ;
- les fingerprints PIT ;
- les ledgers PIT ;
- les règles de validation ;
- les fichiers de sortie de V22.0 ;
- les fichiers de sortie de V22.1 ;
- les divergences V22.0/V22.1 ;
- le holdout ;
- les ordres réels.

T1/T2 restent strictement interdits aux Actions CT.

## Observabilité

Le bundle crée `outputs/audit/ACTION_CT_SHARED_HISTORY_RUNTIME_V21_13_10.json` avec :

- nombre de demandes logiques d'historiques ;
- nombre de lectures physiques Parquet ;
- nombre de lectures physiques évitées ;
- durée de lecture physique ;
- nombre de copies de DataFrame fournies aux consommateurs ;
- statut de V22.0 ;
- statut de V22.1 ;
- durée totale du bundle.

Sur le contrat normal, deux demandes logiques V22.0/V22.1 doivent produire une seule lecture physique du lot Action CT.

## Effet attendu

Le gain réel dépend de la taille et du nombre de fichiers Parquet du cache Actions. Il provient de trois éléments :

1. suppression d'une seconde lecture/parsing complète du lot historique Actions CT ;
2. suppression d'un démarrage Python séparé ;
3. conservation en mémoire des pages déjà chargées pendant l'enchaînement V22.0 -> V22.1.

La télémétrie GitHub reste l'autorité. Aucun gain théorique ne doit être considéré acquis avant plusieurs runs représentatifs.

## Critère de promotion

La modification n'est acceptable que si la CI confirme :

- suite pytest complète verte ;
- TCT vert ;
- Action CT V22 vert ;
- audit Committee Master vert ;
- audit identité vert ;
- aucun changement de gouvernance, poids ou seuil ;
- sorties V22.0 et V22.1 toujours produites par leurs moteurs d'origine.
