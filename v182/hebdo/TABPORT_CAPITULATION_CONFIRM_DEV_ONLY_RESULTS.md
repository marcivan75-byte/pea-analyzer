# TABPORT — Capitulation × confirmation J+1 — résultats développement/OOS

## Gouvernance

- Calibration des seuils : **2010–2022 uniquement**.
- Holdout : **2023–2026**, évaluation uniquement.
- Famille de variantes figée avant lecture du holdout.
- Variables J+1 disponibles au close de confirmation ; entrée TABPORT seulement à l'open de la séance suivante.
- Aucune imputation synthétique.
- Aucune promotion production automatique.

Run de référence : `33600934483` — SUCCESS. Artefact : `9835407118`.
Tests : 65 passés.

## Seuils gelés issus du développement

- drawdown 4 semaines q40 : `-8.6028%`
- vol_z q60 : `0.79515`
- prob_stop_9 q50 : `0.14861`
- rendement close J+1 q50 : `+1.4962%`
- rendement intraday J+1 q50 : `+1.0838%`
- close J+1 depuis le plus bas q50 : `+1.6997%`

## Résultat principal

Baseline holdout 2023–2026 :
- PF `2.042`
- RR `2.246`
- espérance `+4.707%`
- rendement portefeuille segment `+19.082%`
- drawdown max `-9.851%`
- taux de stop `43.81%`

Meilleur résultat agrégé observé parmi les variantes figées : `J1_INTRADAY_GE_DEV_Q50` :
- PF `2.064`
- RR `2.479`
- espérance `+5.466%`
- rendement portefeuille segment `+23.356%`
- drawdown max `-6.992%`
- taux de stop `49.07%`

Sur le développement, ce même filtre reste proche de la baseline : PF `1.474` vs `1.463`, RR `2.595` vs `2.563`, espérance `2.812%` vs `2.725%`, drawdown `-14.93%` vs `-17.82%`, mais rendement cumulé légèrement inférieur (`82.15%` vs `82.98%`).

## Test de stabilité temporelle

L'avantage agrégé OOS n'est **pas stable par année** :

| Année | Baseline | J1 intraday >= q50 dev | Différence |
|---|---:|---:|---:|
| 2023 | +4.38% | +3.20% | -1.18 pt |
| 2024 | +1.62% | +10.41% | +8.79 pt |
| 2025 | +7.92% | +5.34% | -2.58 pt |
| 2026 partiel | +4.04% | +1.94% | -2.10 pt |

Le gain OOS agrégé provient donc principalement de 2024. Sur 2010–2022, la différence annuelle médiane contre baseline est légèrement négative (~`-0.46 pt`) ; sur le holdout, la médiane annuelle est également négative (~`-1.64 pt`).

## Décision

`J1_INTRADAY_GE_DEV_Q50` = **RESEARCH_ONLY / NON PROMU**.

Motif : amélioration convaincante des métriques agrégées et du drawdown, mais absence de robustesse annuelle et taux de stops supérieur. Le résultat ne justifie pas une règle de production.

Les autres variantes (J1 close fort, capitulation profonde + confirmation, volume + confirmation, probabilité de stop + confirmation) dégradent davantage le PF, le rendement ou la stabilité OOS et sont rejetées comme filtres d'entrée production.

## Conséquence méthodologique

Après les études garde-fous titre, régime de marché et capitulation × confirmation, ajouter des filtres d'entrée binaires réduit trop souvent l'univers et modifie l'ordonnancement/capacité du portefeuille sans amélioration robuste. Le prochain levier à étudier doit privilégier **la gestion du risque après entrée / sorties précoces**, avec règles PIT et seuils gelés sur le développement, plutôt qu'une nouvelle couche de filtrage d'entrée.
