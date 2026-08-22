# Pipeline runtime V21.13.11 — cache Parquet brut CT/TCT

Date : 22/08/2026

## Objectif

Réduire encore le temps du run quotidien et du vendredi sans réduire les données, critères, pondérations, seuils, règles PIT, sorties ou contrôles du Comité d'Investissement.

## Principe

Après V21.13.10, Actions CT V22.0 et V22.1 partagent déjà leur chargement historique. TCT V24.3.1 s'exécutait toutefois dans un second processus Python et relisait les mêmes fichiers Parquet `data/cache/actions`.

V21.13.11 exécute désormais dans un seul processus :

1. Actions CT V22.0 ;
2. Actions CT V22.1 ;
3. TCT V24.3.1.

L'ordre métier reste inchangé.

## Niveau de partage

Le partage est placé sous les extracteurs métier, au niveau du seul appel brut `pandas.read_parquet(path)` :

- les extracteurs V22.0/V22.1 restent inchangés ;
- l'extracteur TCT reste inchangé ;
- le premier accès physique à un fichier Parquet est mémorisé pendant le processus ;
- chaque lecture logique reçoit une copie profonde indépendante du DataFrame brut ;
- aucune transformation, sélection de ticker, indicateur ou score n'est partagée entre modèles.

Un appel `read_parquet` utilisant des arguments ou options supplémentaires n'est jamais mis en cache. Il est transmis tel quel au lecteur pandas original. Cette règle évite toute supposition sur l'équivalence de deux lectures paramétrées.

## Contrat commun vérifié

Les trois modules utilisent :

- `data/cache/actions` ;
- OHLCV daily ;
- weekly dérivé du daily ;
- bougies daily complètes uniquement ;
- report de la bougie du jour avant 18 h Europe/Paris ;
- aucun intraday ;
- aucune donnée 5 minutes ;
- aucun nouveau téléchargement de marché demandé par ces moteurs.

## Invariants financiers

Aucun changement sur :

- univers ;
- données sources ;
- critères ;
- poids ;
- seuils ;
- formules ;
- règles d'entrée/sortie ;
- fingerprints ;
- ledgers PIT ;
- états temporels ;
- validations ;
- divergences V22.0/V22.1 ;
- présélection PREMARKET/POSTMARKET ;
- holdout ;
- ordres réels.

T1/T2 restent exclusivement TCT Actions et restent interdits aux Actions CT.

## Tolérance aux erreurs

Le bundle conserve le comportement séquentiel avec confinement des erreurs :

- une erreur Action CT n'empêche pas l'exécution du TCT ;
- une erreur TCT n'efface pas les sorties Action CT déjà produites ;
- l'audit transversal est écrit ;
- le bundle remonte ensuite une erreur globale au workflow `continue-on-error`.

Le lecteur `pandas.read_parquet` original est restauré dans un bloc `finally`.

## Observabilité

`outputs/audit/TACTICAL_SHARED_PARQUET_RUNTIME_V21_13_11.json` mesure :

- appels logiques `read_parquet` ;
- lectures physiques ;
- cache hits ;
- appels paramétrés passés directement ;
- nombre de chemins uniques mémorisés ;
- durée cumulée des lectures physiques ;
- statut Action CT ;
- statut TCT ;
- durée totale du bundle.

## Gain attendu

Le gain vient de :

- la suppression d'un démarrage Python séparé pour TCT V24.3.1 ;
- la suppression des relectures physiques des fichiers Actions déjà lus pour CT ;
- la conservation en mémoire des pages Parquet pendant la chaîne CT -> TCT.

Aucune estimation de gain n'est promue comme acquise avant télémétrie d'un run représentatif. Les durées GitHub observées restent l'autorité.
