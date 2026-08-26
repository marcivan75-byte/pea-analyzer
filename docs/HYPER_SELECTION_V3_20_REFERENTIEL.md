# Hyper-Selection V3 — 20 critères non redondants

## Objet

Cette version s'inspire de la proposition à 32 critères sans réintroduire les signaux déjà synthétisés par
TradingView, Boursorama ou les piliers existants. Elle reste `shadow_only`, ne crée aucun candidat et ne
modifie pas les scores CI ou CI LIGHT.

## Pondération

| ID | Critère | Poids |
|---|---|---:|
| H01 | TradingView multi-horizon | 8 |
| H02 | Boursorama confirmation | 7 |
| H03 | RSI individuel | 3 |
| H04 | Potentiel central | 9 |
| H05 | Qualité | 8 |
| H06 | Croissance persistante | 5 |
| H07 | Valeur ou coût | 5 |
| H08 | Bilan ou tracking | 5 |
| H09 | Risque global | 6 |
| H10 | Liquidité absolue | 4 |
| H11 | Révisions ou flux | 4 |
| H12 | Diversification | 3 |
| H13 | Macro et secteur | 3 |
| H14 | Catalyseurs agrégés | 2 |
| H15 | Qualité des données | 2 |
| H16 | Reward/Risk explicite | 8 |
| H17 | Fiabilité des preuves | 6 |
| H18 | Stabilité temporelle | 3 |
| H19 | Résistance downside | 6 |
| H20 | Liquidité relative | 3 |
| **Total** |  | **100** |

## Apports issus de la proposition V2-32

- H16 estime l'asymétrie objectif/risque avant la simulation complète, sans dépendance circulaire.
- H17 mesure la qualité des preuves et pénalise explicitement une synthèse TradingView manquante.
- H18 exige des observations datées; une relance le même jour ne crée pas une observation supplémentaire.
- H19 agrège drawdown, volatilité baissière disponible et bêta sans dupliquer H09.
- H20 combine volume relatif et spread; une couverture partielle reste signalée.

## Critères non repris individuellement

- `perf_1m`, `perf_3m`, `perf_6m`, MACD, reversal et distance SMA200 restent agrégés dans les piliers de
  tendance ou TradingView afin d'éviter le double comptage.
- score consensus, révisions et largeur analystes restent regroupés entre Boursorama et H11.
- Morningstar reste un fallback ETF 4–5 étoiles, jamais un score principal.
- RSI reste individuel à la demande de gouvernance, pour identifier survente et surachat.

## Gates inchangés

Coverage minimale 70 %, score initial 62, potentiel Actions minimal 20 %, règles TradingView/Boursorama,
anti-recouvrement ETF et absence d'ordres réels.
