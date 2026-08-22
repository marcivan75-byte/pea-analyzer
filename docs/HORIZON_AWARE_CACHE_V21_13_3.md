# Horizon-aware cache V21.13.3

## Principe

La fréquence de collecte réseau dépend désormais du type de donnée et de l'horizon qui la consomme. L'univers de scoring reste complet et aucune pondération, aucun seuil, aucun critère ni aucune règle de décision n'est modifié.

## Hiérarchie

| Famille de données | Horizons consommateurs | Priorité de fraîcheur |
|---|---|---|
| OHLCV / prix / volume | TCT, CT, MT, LT | maximale, chaque journée de marché |
| Top-down / news | TCT, CT, TOP_DOWN | maximale, chaque run |
| Consensus Actions | CT, MT, LT | CT > MT > LT |
| Fondamentaux Actions | CT, MT, LT | CT > MT > LT |
| Informations ETF | CT, MT, LT | CT > MT > LT, cadence structurelle plus lente |
| ETF Fund Flows | ETF MT, rotation sectorielle | hebdomadaire / nouvel `as_of` |
| Références structurelles | LT, structurel | lente, typiquement 30 jours |

TCT ne force donc plus le rafraîchissement de données fondamentales ou de consensus qu'il ne consomme pas. Il conserve en revanche la priorité absolue sur les données de marché et les événements courts.

## Attribution HOT / WARM / COLD

Quand un résultat de comité précédent est disponible, les meilleurs candidats de chaque horizon sont utilisés uniquement pour ordonner la collecte du run suivant :

- CT prioritaire -> HOT ;
- MT prioritaire -> WARM ;
- LT et reste de l'univers -> COLD.

Un buffer de promotion basé sur le score courant empêche un titre émergent de rester COLD entre deux runs. Les statuts COMMITTEE/WATCH et les publications de résultats proches surclassent toujours la politique d'économie de cache.

Si le fichier de décisions précédent est absent ou invalide, le système revient automatiquement au score courant, aux événements et au cache existant. Il ne bloque pas le run.

## TTL et budgets V21.13.3

### Yahoo fondamentaux Actions

- HOT : 3 jours ;
- WARM : 10 jours ;
- COLD : 21 jours ;
- budget normal maximal : 320 rafraîchissements ;
- bootstrap de toute donnée absente conservé ;
- âge dur maximal : 35 jours.

### Finnhub

Recommandations :
- HOT 2 jours ; WARM 7 jours ; COLD 14 jours.

Objectifs de cours :
- HOT 5 jours ; WARM 14 jours ; COLD 28 jours.

Budgets recommandation : 100 / 60 / 25. Budgets target : 50 / 30 / 10.

### Yahoo ETF info

Un cache dédié est ajouté : `state/provenance/source_cache/YFINANCE_ETF_INFO_V1.json`.

- HOT : 7 jours ;
- WARM : 14 jours ;
- COLD : 30 jours ;
- budget normal maximal : 40 ETF ;
- bootstrap complet si le cache est absent ;
- âge dur maximal : 45 jours.

## Gouvernance

- 1 829 Actions et 102 ETF restent dans les univers canoniques.
- La hiérarchie pilote seulement la fréquence des appels réseau.
- Une donnée mise en cache conserve son timestamp d'origine.
- Les données manquantes ne sont jamais inventées ni imputées en neutre.
- Les indicateurs dépendant du prix peuvent continuer à être recalculés localement depuis OHLCV + dénominateurs fondamentaux.
- T1/T2 restent exclusivement Action TCT.
- ETF Fund Flows reste SHADOW.
- Les gains de temps réels doivent être mesurés par la télémétrie des prochains runs.
