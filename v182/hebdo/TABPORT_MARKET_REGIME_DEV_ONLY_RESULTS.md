# TABPORT — étude des régimes de marché développement-only

Date : 2026-09-02
Run : `33598438996`
Commit exécuté : `c803b31caf4cf2e4761f13834562ddce908d06cd`
Artefact : `TABPORT-MARKET-REGIME-33598438996` / ID `9834475570`

## Gouvernance

Les variables de régime sont calculées strictement à `market_snapshot_date`, donc T-1 par rapport à la décision J+1 :

- proportion de l'univers au-dessus de la SMA200 ;
- proportion de l'univers avec rendement 20 séances positif ;
- rendement médian 20 séances de l'univers.

Les seuils sont fixés exclusivement sur 2010-2022, puis gelés avant lecture du holdout 2023-2026. Aucune imputation synthétique. Aucune promotion production automatique.

Seuils développement :

- breadth SMA200 q40 = `0.4923896499` ;
- breadth SMA200 q50 = `0.5502392344` ;
- breadth rendement 20 séances q40 = `0.4206036745`.

## Résultats holdout 2023-2026

| Modèle | Trades | Win % | Espérance % | PF | RR | Stops % | P&L clôturé € | Rendement NAV segment % | DD max % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BASELINE | 105 | 47.62 | 4.71 | 2.04 | 2.25 | 43.81 | 22,067.87 | 19.08 | -9.85 |
| Breadth SMA200 >= q40 | 93 | 32.26 | 0.08 | 1.01 | 2.12 | 61.29 | 352.19 | 0.35 | -14.07 |
| Breadth SMA200 >= q50 | 63 | 34.92 | 2.09 | 1.33 | 2.49 | 60.32 | 5,842.03 | 5.99 | -13.66 |
| Breadth ret20 positif >= q40 | 113 | 35.40 | 2.32 | 1.39 | 2.53 | 56.64 | 11,722.10 | 14.72 | -12.40 |
| Rendement médian 20 séances >= 0 | 107 | 38.32 | 2.84 | 1.49 | 2.39 | 54.21 | 13,560.83 | 14.59 | -10.81 |
| Breadth SMA200 q40 + median ret20 positif | 90 | 33.33 | 0.92 | 1.15 | 2.29 | 61.11 | 3,759.02 | 4.00 | -14.10 |

## Décision

Tous les filtres simples de « marché sain » sont rejetés comme garde-fous actifs.

Le constat est structurel : la chaîne B_V2 -> META -> J+1 exploite des situations de capitulation/rebond. Imposer que le marché soit déjà largement au-dessus de ses moyennes ou déjà positif à 20 séances élimine une partie des meilleurs retournements, tout en laissant subsister des mauvais signaux.

L'exemple le plus net est le filtre breadth SMA200 q40 : le rendement NAV OOS tombe de +19.08% à +0.35%, le PF de 2.04 à 1.01 et le taux de stop monte de 43.81% à 61.29%.

Les petites améliorations observées sur certaines années de développement ne survivent pas au holdout. Elles ne doivent donc pas être interprétées comme un alpha reproductible.

## Suite de recherche

La prochaine recherche ne doit plus chercher à interdire les marchés faibles. Elle doit distinguer la **qualité d'une capitulation individuelle et de son rebond J+1**.

Axes autorisés, tous disponibles PIT :

- profondeur du drawdown 4 semaines ;
- intensité du choc de volume (`vol_z`) ;
- ATR ;
- position relative à la SMA200 ;
- force du rebond J+1 : gap, rendement intraday, distance au plus bas, score de confirmation ;
- interactions simples et interprétables entre choc initial et qualité de confirmation.

Les règles resteront calibrées exclusivement sur 2010-2022 et évaluées gelées sur 2023-2026.
