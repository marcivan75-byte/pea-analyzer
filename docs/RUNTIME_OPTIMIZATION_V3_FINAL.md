# PEA Analyzer — Runtime Optimization V3 Final

## Contrat de non-dégradation

Cette V3 optimise uniquement le runtime et les I/O. Elle ne modifie aucun critère financier, aucune pondération, aucun seuil, aucun univers, aucune règle TCT/ETF/Gold, aucune règle PIT/anti-look-ahead, aucune exigence de provenance, aucune logique du Comité ni la profondeur du CI complet.

## Run quotidien

Le run quotidien exécute : collecte, indicateurs, scoring, Comité, reporting, provenance et suivi de performance virtuelle. Il n'exécute aucun backtest de recherche, walk-forward, calibration de pondérations ou recherche de seuils.

## Optimisations V3 intégrées

1. Cache OHLCV persistant : bootstrap historique complet uniquement si le cache est absent ou incompatible.
2. Rafraîchissement OHLCV quotidien incrémental sur une fenêtre de recouvrement, avec fusion cellule par cellule sans effacer une valeur historique valide.
3. Détection des réajustements Yahoo sur séances clôturées ; reconstruction complète automatique du batch concerné en cas de split, dividende ou correction fournisseur.
4. Cache OHLCV ETF unique partagé entre l'enrichissement général et le module ETF MT.
5. Collecte de structure ETF avec concurrence bornée et rate limiter global : mêmes ETF, mêmes champs, mêmes règles de données manquantes et même cadence maximale de départ des requêtes.
6. GDELT : déduplication exacte des requêtes identiques dans le même Top-Down, puis concurrence bornée avec rate limiter global ; fenêtre 2 jours, nombre maximal d'articles et scoring lexical inchangés.
7. Audit Excel : formatage dans la même session d'écriture afin d'éviter la réouverture et la seconde sauvegarde ; feuilles, données et audits après chaque wave inchangés.
8. GitHub production : restauration/sauvegarde de `data/cache/`, cache pip et suppression de l'upgrade pip systématique.
9. Le timeout de production reste à 180 minutes et la rétention des artefacts reste à 30 jours.
10. Le CI final reste complet : compilation de `src` et `tests`, Ruff, audit statique, intégrité des référentiels/gouvernance, full pytest et artefact d'audit 30 jours.

## Backtests

Les backtests PIT/OOS, walk-forward, calibrations et optimisations sont un process de recherche séparé, déclenché explicitement. Ils ne font jamais partie du run quotidien normal du Comité.

## Promotion

La V3 ne doit être fusionnée que si le HEAD final de la PR passe le full CI complet. Toute modification de code postérieure à un CI vert impose un nouveau full CI.
