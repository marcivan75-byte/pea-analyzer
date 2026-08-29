# AT WEEKLY V1.1 — résultats corrigés

Date : 2026-08-29  
Branche : `research/at-weekly-v1-20260829`  
Run GitHub Actions : `33257197637`  
Statut : **SUCCESS — research only**

## Correction technique

Le cache OHLCV est consolidé en blocs Parquet à colonnes MultiIndex `(ticker, champ)`, et non en un fichier par instrument. Le lecteur V1.1 charge chaque bloc une seule fois, détecte automatiquement l'orientation ticker/champ, puis extrait les historiques individuels sans modifier les règles de stratégie.

- 18 blocs Actions
- 3 blocs ETF
- 1 892 instruments bruts
- 1 837 instruments avec historique hebdomadaire exploitable
- 1 739 Actions valides
- 98 ETF valides
- fenêtre des semaines complètes : 2023-01-06 → 2026-08-21
- runtime du moteur : 44,51 s
- paramètres de stratégie modifiés : **non**

## Règles testées, inchangées

Entrée, toutes conditions réunies : RSI(14) < 60 ; Stochastique 14,3,3 croisement haussier %K/%D ; cours > MM20 ; cours > MM50 ; cours > Parabolic SAR 0,02/0,20.

Sortie, dès qu'une condition est atteinte : RSI(14) > 75 ; Stochastique %K > 75 ; cours < MM20 ; cours < MM50 ; cours < SAR.

Signal sur clôture hebdomadaire complète ; exécution à l'ouverture de la semaine suivante. Frais et slippage non appliqués dans ce diagnostic.

## Résultats Actions

| Fenêtre | Trades | Taux positif | Rendement moyen/trade | Médiane | Profit factor | P10 | Durée moyenne |
|---|---:|---:|---:|---:|---:|---:|---:|
| 12 mois | 964 | 42,63 % | +0,274 % | -0,360 % | 1,101 | -8,062 % | 2,12 sem. |
| 18 mois | 1 462 | 43,23 % | +0,385 % | -0,358 % | 1,135 | -8,780 % | 2,15 sem. |
| 24 mois | 1 915 | 42,92 % | +0,154 % | -0,368 % | 1,055 | -8,503 % | 2,13 sem. |
| 36 mois / tout historique exploitable | 2 489 | 43,43 % | +0,106 % | -0,347 % | 1,038 | -8,418 % | 2,17 sem. |

Extrêmes Actions : meilleur trade +171,567 % ; pire trade -57,143 %. La moyenne est donc influencée par quelques très gros gagnants alors que la médiane reste négative.

## Résultats ETF

| Fenêtre | Trades | Taux positif | Rendement moyen/trade | Médiane | Profit factor | P10 | Durée moyenne |
|---|---:|---:|---:|---:|---:|---:|---:|
| 12 mois | 107 | 63,55 % | +0,651 % | +0,615 % | 2,176 | -2,395 % | 1,50 sem. |
| 18 mois | 135 | 68,89 % | +0,481 % | +0,577 % | 1,816 | -2,182 % | 1,41 sem. |
| 24 mois | 192 | 65,10 % | +0,676 % | +0,577 % | 2,045 | -2,254 % | 1,45 sem. |
| 36 mois / tout historique exploitable | 212 | 63,21 % | +0,593 % | +0,523 % | 1,895 | -2,254 % | 1,46 sem. |

Extrêmes ETF : meilleur trade +9,363 % ; pire trade -19,187 %.

## Diagnostic des filtres d'entrée

Sur 243 744 semaines-instruments éligibles aux indicateurs :

1. RSI < 60 : 181 507 observations (74,5 % du niveau précédent)
2. + croisement haussier du Stochastique : 21 471 (11,8 %)
3. + cours > MM20 et MM50 : 5 360 (25,0 %)
4. + cours > SAR : 2 916 (54,4 %)

Le signal final ne représente qu'environ 1,2 % des semaines-instruments observées.

## Diagnostic des sorties

Nombre de trades dont la règle apparaît dans le signal de sortie :

- Stochastique %K > 75 : 1 586
- cours < MM20 : 841
- cours < MM50 : 568
- cours < SAR : 375
- RSI > 75 : 25

Les sorties exclusivement déclenchées par `%K > 75` sont majoritairement gagnantes : Actions 1 326 trades, 70,5 % positifs, rendement moyen +3,71 % ; ETF 178 trades, 74,7 % positifs, rendement moyen +1,19 %.

Le point faible principal est donc l'entrée Actions, pas la sortie Stochastique : les trades Actions sortant après une semaine sont 1 353, avec 40,1 % de trades positifs et un rendement moyen de -0,98 %.

## Conclusion V1.1

- **ETF : signal initial intéressant**, cohérent sur 12/18/24/36 mois, mais encore diagnostic et non OOS promotionnel.
- **Actions : signal insuffisant en l'état**. Taux positif <44 %, médiane négative et profit factor proche de 1. Il faut améliorer la qualité des entrées avant toute utilisation.
- Ne pas relâcher les sorties sur la seule base de ce run : le Stochastique >75 capture actuellement une grande part des trades gagnants.

## Limites

Univers du cache courant et non appartenance historique PIT ; biais de survivance possible ; frais/slippage absents ; diagnostic pré-OOS. Aucun changement automatique de seuil, aucune influence sur le Comité d'investissement, aucun ordre réel.
