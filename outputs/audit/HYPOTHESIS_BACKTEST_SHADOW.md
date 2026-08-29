# Backtest diagnostic des hypothèses entrée (proxy PIT)

**Pas une promotion.** Signal = momentum prix 126j du cache 27/08, pas le score CI ni la confiance 66.
Horizon 60 séances, 44 snapshots à 21 jours, 2023-07-13 → 2026-05-18.
R:R book historique absent.

Sur les Actions, la moyenne EW (27 %) est tirée par des queues épaisses : lire la **médiane** et le P10.

## ACTION_MT (1750 titres)

| politique | mean 60j % | médiane | P10 | hit-rate % |
|---|---|---|---|---|
| Equal weight | 27.35 | 5.41 | -2.14 | 79.5 |
| Top 10% statique | 16.87 | 4.36 | -3.76 | 70.5 |
| Dynamique P70 | 8.36 | 5.25 | -1.83 | 86.4 |

## ETF_MT (100 titres)

| politique | mean 60j % | médiane | P10 | hit-rate % |
|---|---|---|---|---|
| Equal weight | 4.42 | 4.14 | 0.39 | 93.2 |
| Top 10% statique | 4.76 | 5.47 | -1.84 | 79.5 |
| Dynamique P70 | 4.58 | 4.72 | -0.66 | 88.6 |

Lecture: le P70 améliore le taux de réussite Actions et réduit la queue gauche vs top 10%.
Il ne bat pas l'equal-weight ETF en hit-rate. Aucune hypothèse n'est promue.
