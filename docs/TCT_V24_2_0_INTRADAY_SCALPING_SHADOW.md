# TCT V24.2.0 — Intraday / Scalping SHADOW

Date : 18/08/2026

## 1. Objet

TCT V24.2.0 ajoute une couche de micro-timing intraday au module ACTION TCT.
Le terme « scalping » désigne ici l'emprunt de techniques de timing à très
court terme pour améliorer l'observation de l'entrée TCT ; il ne crée pas un
système de scalping autonome et n'autorise aucun ordre réel.

Le CT, les ETF et les autres horizons restent hors périmètre.

## 2. Pourquoi V24.2.0

`V24.1.8` existe déjà comme challenger de normalisation dynamique de la
baseline TCT. La couche intraday utilise donc une nouvelle famille de version
afin de ne pas mélanger deux hypothèses statistiques distinctes.

## 3. Architecture

Chaîne de recherche :

`PEA ACTION -> baseline TCT -> T1/T2 exact -> ledger horodaté -> J+1..J+3 intraday 5m -> diagnostic SHADOW`

La couche V24.2.0 ne modifie ni `tct_adapter`, ni les décisions canoniques,
ni V21.8 Entry/Exit.

### Règle anti-look-ahead

T1/T2 exact utilise une bougie journalière complète. Un signal observé à la
clôture de J ne peut donc pas utiliser les barres intraday de J.

Règle normative V24.2.0 :

- même session que le signal : interdite ;
- première session éligible : J+1 ;
- fenêtre de recherche initiale : J+1 à J+3 ;
- les observations sont persistées dans un ledger PIT ;
- MFE, MAE et rendement à la clôture sont des labels d'issue calculés uniquement
  après un événement d'entrée causal ; ils ne participent jamais au signal.

## 4. Données et features

Source de bootstrap : cache intraday séparé `data/cache/actions_intraday_5m`.

Granularité initiale : 5 minutes.

Features causales :

- VWAP cumulatif de séance ;
- distance et pente du VWAP ;
- RVOL par tranche horaire, comparé uniquement aux séances antérieures ;
- accélération du volume ;
- turnover et turnover relatif ;
- opening range, actionnable seulement après sa clôture ;
- plus haut/bas antérieur glissant avec décalage d'une barre ;
- ATR intraday et expansion du range ;
- momentum 1/3 barres et EMA9 ;
- spread si bid/ask est fourni ;
- imbalance si bid_size/ask_size est fourni.

Le carnet et l'order flow restent optionnels : leur absence réduit la
couverture disponible mais n'est jamais remplacée par une donnée inventée.

## 5. Setups testés

1. `EXPLOSIVE_BREAKOUT`
2. `BREAKOUT_RETEST`
3. `VWAP_RECLAIM`
4. `OPENING_RANGE_BREAKOUT`

Les quatre familles sont séparées dans les observations afin de mesurer leur
gain marginal. Aucune n'est promue sur la base d'une intuition.

## 6. Score diagnostique SHADOW

Baseline pré-enregistrée :

- RVOL / accélération volume : 20 %
- VWAP / timing : 15 %
- structure : 20 %
- volatilité intraday : 15 %
- liquidité / exécution : 15 %
- momentum 5m : 10 %
- order flow optionnel : 5 %

Les critères disponibles sont renormalisés pour le diagnostic ; une couverture
pondérée minimale de 65 % est exigée pour un état d'entrée SHADOW.

Seuils de recherche initiaux :

- `ENTRY_READY_SHADOW` : score >= 72
- `ENTRY_STRONG_SHADOW` : score >= 82
- RVOL de confirmation : >= 1,20
- expansion de range : >= 1,10
- extension maximale au-dessus du VWAP : paramètre diagnostique 2,5 %

Ces valeurs sont des hypothèses de départ gelées pour le premier jeu de
mesures ; elles ne sont pas des règles de production.

## 7. Risque et sorties

Aucun take-profit fixe et aucun stop-loss fixe ne sont activés.

Le moteur peut enregistrer une référence d'invalidation structurelle pour
analyse, ainsi que sa distance au prix d'entrée SHADOW. Cette référence a une
influence de stop égale à zéro.

Les futurs labels de trajectoire sont :

- `ACCELERATION`
- `NORMAL_PULLBACK`
- `MOMENTUM_STALL`
- `FAILED_BREAKOUT`
- `STRUCTURAL_INVALIDATION`
- `REACCELERATION`

Ils restent des variables de recherche jusqu'à validation spécifique.

## 8. Persistance PIT

Deux états sont conservés :

- `state/TCT_V24_2_0_SIGNAL_LEDGER.csv`
- `state/TCT_V24_2_0_INTRADAY_OBSERVATIONS.csv`

Le premier fige les signaux T1/T2 et leur date de connaissance.
Le second fige les diagnostics intraday et les outcomes observés.

Les deux fichiers sont restaurés/sauvegardés par le workflow quotidien et
publiés dans l'artefact tactique pour audit.

## 9. Runtime

Le workflow `committee_tct_ct_daily.yml` exécute V24.2.0 après le run
TCT/CT canonique.

La commande SHADOW utilise `continue-on-error: true` :

- une panne de données intraday ne bloque pas le TCT/CT de production ;
- l'audit devient `DEGRADED_INTRADAY_DATA` si nécessaire ;
- aucun module lourd supplémentaire n'est déclenché.

La cadence actuelle reste post-clôture. Cette phase sert à accumuler un jeu
PIT propre avant toute tentative de runtime intraday réellement actionnable.

## 10. Gouvernance

Valeurs obligatoires :

- `decision_influence = 0.0`
- `score_influence = 0.0`
- `sizing_execution_influence = 0.0`
- `stop_loss_influence = 0.0`
- `ct_influence = 0.0`
- `real_orders_enabled = false`
- `fixed_take_profit_enabled = false`
- `fixed_stop_loss_enabled = false`
- `holdout_locked = true`

Le holdout final reste fermé.

## 11. Promotion éventuelle

Après accumulation suffisante, chaque setup devra être évalué séparément sur :

- espérance nette ;
- profit factor ;
- taux de réussite ;
- gain moyen / perte moyenne ;
- MFE / MAE ;
- drawdown et queue de pertes ;
- faux breakouts ;
- stabilité par heure de séance ;
- stabilité par régime ;
- concentration par émetteur ;
- sensibilité aux frictions.

Une hypothèse gagnante sera ensuite gelée avant validation PIT/OOS. Aucun seuil
ne sera ajusté sur le holdout final.

Le CT ne pourra recevoir une variable issue de V24.2.0 qu'après démonstration
spécifique de son gain marginal sur CT.
