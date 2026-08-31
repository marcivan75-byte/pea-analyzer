# HEBDO AT CHAT V22.1

## Statut

Implémentation de recherche gouvernée sur la branche `v22/pit-mae-mfe-preopen`. Aucune promotion de métrique n'est autorisée sans backtest historique PIT 2019-2024 effectivement alimenté.

## Architecture

- vrai P&L forward avec stop intraday -9 % et MAE/MFE ;
- B1 V2.1 : z-score volume > 3, baisse daily < -1,5 %, close < SMA20 ; B2 dès J+1 ;
- PIT strict T-1 22:00 Europe/Paris sur timestamps d'observation ;
- IC Spearman + LassoCV avec poids gouvernés ;
- PREOPEN actif borné à 20 TCT + 20 CT ;
- filtre ETF TER/actifs gouverné ;
- score HEBDO sector-neutral ;
- filtre MAE `stop_prob > 0.45 => EXCLU_MAE` ;
- données MAE manquantes => `BLOCK_DATA_MAE`, sans imputation ;
- secteur absent => `BLOCK_DATA_SECTOR` ;
- feature gouvernée absente => `BLOCK_DATA_FEATURE/WEIGHTS` ;
- qualité : `ROE < 5 %` et `debt_to_equity > 1.5` => `EXCLU_QUALITE`, données manquantes => `BLOCK_DATA_QUALITY` ;
- double tri top 2 par secteur puis ranking global sector-neutral ;
- sizing inverse ATR sans valeur par défaut ;
- régime CAC40 CRASH si retour 2 semaines < -3 %, TCT max 10 et 20 % cash ;
- contrôle de sortie à 4 semaines si momentum sector-neutral < 0 ;
- IC decay 1w / 2w / 4w / 13w / 26w ;
- dashboard comité avec hit-rates vrais, MAE, stop-rate, turnover, espérance et statut des gates.

## Backtest historique

Le runner `v182.hebdo.backtest_v22_1` requiert deux entrées réelles :

1. snapshots de features historiques avec timestamp PIT prouvé ;
2. OHLCV quotidien historique correspondant.

Il refuse les features sans timestamp PIT ou dont le timestamp est futur, calcule les forward returns vrais 1w/2w/4w/13w/26w en tenant compte du stop intraday, puis entraîne IC/Lasso uniquement si au moins 100 lignes complètes sont disponibles.

Aucun poids final n'est généré si ces données ne sont pas disponibles.

## Critères d'acceptation à mesurer

- IC 1w > 0,06 ;
- IC 4w > 0,03 ;
- hit-rate 26w vrai > 60 % ;
- stops < 20 % ;
- turnover < 35 % ;
- MAE moyen >= -4,5 % ;
- espérance vraie 26w >= +7,8 %.

Ces valeurs sont des objectifs, pas des résultats acquis.

## Validation

Le workflow `V22 PIT True PnL PREOPEN validation` compile les modules V22.1, exécute les tests critiques dédiés et l'audit `master_data --fail-fatal` avant la suite globale de compatibilité legacy.

Le workflow `HEBDO AT CHAT V22.1 Friday` est planifié vendredi 22:00 Europe/Paris. Il reste fail-closed si les features PIT, les poids gouvernés ou l'historique CAC40 PIT ne sont pas présents dans l'état restauré ; dans ce cas il publie un audit `BLOCK_DATA` et aucune sélection.

## Gouvernance

- aucune clé API en clair ;
- aucun ISIN/ticker inventé ;
- aucun fallback vers master courant pour reconstruire le passé ;
- aucune imputation silencieuse de secteur, ATR, fondamentaux ou feature Lasso ;
- aucun résultat cible présenté comme atteint sans artefact de backtest correspondant ;
- aucun ordre réel activé par ce module.
