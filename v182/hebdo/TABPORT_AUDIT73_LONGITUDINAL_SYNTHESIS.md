# TABPORT enrichi — synthèse longitudinale Audit 73

Date de consolidation : 2026-09-02
Branche : `hebdo-at-meta`
Run de référence : `33596161484`
Commit : `4c3fd37ba6544406429ddc19604888d20e18ec1c`
Artefact : `TABPORT-AUDIT73-LONGITUDINAL-V2-33596161484` — ID `9833753814`

## 1. Conclusion exécutive

Le run longitudinal 2010-2026 est **SUCCESS** et valide techniquement la chaîne :

`B_V2 -> META -> confirmation J+1 -> TABPORT stop fixe -9%`

avec exclusion fail-closed des données OHLCV invalides, absence d'imputation synthétique et séparation gouvernée 2010-2022 développement / 2023-2026 holdout-OOS.

Le meilleur modèle descriptif publié est **BASELINE**. Cette conclusion ne signifie cependant pas que les variantes consensus/objectif sont moins performantes : elles n'ont **jamais pu être appliquées** sur un signal du backtest, car aucun des 4 380 signaux confirmés ne dispose d'un snapshot consensus certifié disponible PIT à sa date de décision.

Les variantes suivantes sont donc toutes strictement identiques à BASELINE dans ce run :

- objectif médian >20%;
- objectif >20% + consensus positif;
- + amélioration du consensus;
- + nombre d'analystes >=5, >=10, >=15 ou >=20.

Elles doivent être classées **NON DISCRIMINABLES SUR L'HISTORIQUE CERTIFIÉ ACTUEL**, et non « rejetées ».

## 2. Pourquoi les variantes consensus ne différencient aucun trade

Le run a récupéré 9 artefacts sources réels issus de 16 runs CI LIGHT réussis depuis le 22 août 2026.

Historique consensus effectivement exploitable :

- 63 snapshots certifiés;
- 7 symboles;
- première disponibilité : 2026-08-26 12:44 UTC;
- dernière disponibilité : 2026-08-27 19:14 UTC;
- dates relatives FactSet non transformées en dates historiques artificielles.

Le dernier signal J+1 mature du longitudinal est daté du **2 mars 2026**. La raison est la règle de maturité TABPORT de **126 séances**, qui exclut les candidats trop récents pour lesquels l'issue complète n'est pas encore observable.

Il n'existe donc aucun chevauchement temporel entre :

- les signaux matures évaluables du backtest, qui s'arrêtent début mars 2026;
- les snapshots consensus certifiés, qui commencent fin août 2026.

Diagnostic de chaque variante fondamentale/consensus :

- signaux disposant du critère : **0**;
- signaux avec critère indisponible et pass-through : **4 380**;
- rejets réellement causés par le filtre : **0**;
- imputation : **aucune**.

Cette situation respecte la consigne : lorsqu'un critère non remplaçable est indisponible, le modèle technique continue à être traité et l'absence est commentée explicitement.

## 3. Qualité des données

### Développement 2010-2022

- lignes en entrée : 4 055 044;
- lignes fiables utilisées : 4 055 044;
- lignes rejetées : 0;
- doublons rejetés : 0;
- tickers utilisables : 1 623;
- période : 2010-01-04 à 2022-12-30;
- imputation : non.

### Holdout 2023-2026

- lignes en entrée : 1 580 861;
- lignes rejetées comme non fiables : **2 143**;
- lignes fiables utilisées : 1 578 718;
- doublons rejetés : 0;
- tickers utilisables : 1 782;
- période : 2023-01-02 à 2026-08-27;
- imputation : non.

Aucune donnée rejetée n'a été réparée ou complétée artificiellement.

## 4. Résultat global BASELINE 2010-2026

| Indicateur | Résultat |
| --- | ---: |
| Trades | 556 |
| Gains | 214 |
| Pertes / faux positifs | 342 |
| Taux de gain | 38,49% |
| Gain moyen | +22,64% |
| Perte moyenne | -9,13% |
| Espérance / trade | +3,10% |
| Profit Factor | 1,55 |
| RR / payoff | 2,48 |
| Stops | 310 |
| Taux de stops | 55,76% |
| MAE moyen | -7,95% |
| MAE pire | -44,95% |
| MFE moyen | +17,39% |
| MFE meilleur | +448,44% |
| P&L net cumulé | +76 816,88 EUR |
| Frais | 10 075,07 EUR |
| Capital 65 kEUR -> NAV finale | 141 816,88 EUR |
| Rendement cumulé | +118,18% |
| Drawdown maximal | -17,82% |
| Durée moyenne | 75,38 séances |

Sur la période 2010-01-04 à 2026-08-27, le rendement cumulé de +118,18% correspond à environ **+4,8% annualisé**, très inférieur à ce que suggère la seule lecture du rendement cumulé.

## 5. Stabilité 2010-2023

- 14 années observées;
- 10 années à rendement NAV positif;
- rendement annuel moyen arithmétique : +4,81%;
- dispersion annuelle : 10,12 points;
- pire année : -13,89%.

Le modèle est fortement dépendant du régime de marché. Les performances ne sont pas suffisamment homogènes pour conclure à une stabilité structurelle élevée.

## 6. TABPORT enrichi — 2010-2023 année par année

Les chiffres ci-dessous sont ceux de BASELINE et, faute de couverture PIT du consensus, de toutes les variantes enrichies.

| Année | Trades | G/P | Win% | Espérance | PF | RR | Stops | P&L trades clôturés | Rendement NAV | DD max |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2010 | 3 | 0/3 | 0,0% | -9,45% | 0,00 | n/a | 3 | -1 172,64 EUR | +8,41% | -2,88% |
| 2011 | 41 | 9/32 | 21,95% | -2,91% | 0,61 | 2,15 | 31 | -5 347,48 EUR | -13,89% | -17,82% |
| 2012 | 34 | 17/17 | 50,0% | +5,45% | 2,22 | 2,22 | 15 | +8 312,97 EUR | +11,71% | -6,49% |
| 2013 | 31 | 21/10 | 67,74% | +9,87% | 4,23 | 2,01 | 10 | +13 741,69 EUR | +18,13% | -6,79% |
| 2014 | 37 | 13/24 | 35,14% | +1,90% | 1,34 | 2,47 | 21 | +3 159,23 EUR | +4,09% | -9,11% |
| 2015 | 42 | 13/29 | 30,95% | +2,95% | 1,47 | 3,25 | 27 | +5 656,36 EUR | +11,58% | -9,00% |
| 2016 | 41 | 13/28 | 31,71% | +0,36% | 1,06 | 2,28 | 26 | +670,13 EUR | -2,46% | -11,22% |
| 2017 | 26 | 14/12 | 53,85% | +9,92% | 3,50 | 2,99 | 10 | +11 604,93 EUR | +11,81% | -2,06% |
| 2018 | 51 | 11/40 | 21,57% | -3,33% | 0,54 | 1,96 | 34 | -7 571,83 EUR | -10,79% | -12,66% |
| 2019 | 24 | 14/10 | 58,33% | +9,87% | 3,57 | 2,69 | 9 | +9 886,56 EUR | +14,83% | -2,38% |
| 2020 | 39 | 12/27 | 30,77% | +2,32% | 1,34 | 3,02 | 27 | +4 070,00 EUR | +10,26% | -8,22% |
| 2021 | 30 | 18/12 | 60,0% | +15,84% | 6,03 | 4,03 | 9 | +21 244,39 EUR | +11,28% | -3,70% |
| 2022 | 49 | 6/43 | 12,24% | -6,03% | 0,29 | 2,11 | 42 | -13 154,02 EUR | -11,97% | -14,72% |
| 2023 | 31 | 11/20 | 35,48% | -0,27% | 0,95 | 1,74 | 18 | -406,44 EUR | +4,38% | -5,62% |

### Lecture de stabilité

Années particulièrement solides : 2013, 2017, 2019 et 2021.

Années problématiques : 2011, 2018 et 2022; 2023 présente également une espérance des trades légèrement négative malgré une NAV annuelle positive.

Le millésime 2022 est le principal signal d'alerte : 49 trades, seulement 6 gains, 42 stops, PF 0,29 et espérance -6,03%.

## 7. TABPORT enrichi — 2023-2026 trimestre par trimestre

| Trimestre | Trades | G/P | Win% | Espérance | PF | RR | Stops | P&L trades clôturés | Rendement NAV | DD max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2023Q1 | 13 | 3/10 | 23,08% | -1,02% | 0,86 | 2,86 | 10 | -598,67 EUR | -0,88% | -4,45% |
| 2023Q2 | 2 | 0/2 | 0,0% | -9,70% | 0,00 | n/a | 2 | -864,27 EUR | +2,39% | -2,71% |
| 2023Q3 | 7 | 4/3 | 57,14% | +2,71% | 1,77 | 1,33 | 2 | +847,84 EUR | -1,84% | -3,22% |
| 2023Q4 | 9 | 4/5 | 44,44% | +0,60% | 1,10 | 1,40 | 4 | +208,66 EUR | +5,20% | -2,00% |
| 2024Q1 | 7 | 3/4 | 42,86% | -0,49% | 0,89 | 1,17 | 3 | -135,65 EUR | +0,38% | -2,21% |
| 2024Q2 | 10 | 4/6 | 40,0% | +5,39% | 1,97 | 3,01 | 5 | +2 328,65 EUR | -0,43% | -2,73% |
| 2024Q3 | 7 | 5/2 | 71,43% | +6,53% | 3,24 | 1,30 | 2 | +2 042,85 EUR | +1,20% | -4,58% |
| 2024Q4 | 7 | 4/3 | 57,14% | +7,59% | 3,69 | 2,78 | 2 | +2 382,60 EUR | +0,75% | -1,94% |
| 2025Q1 | 9 | 5/4 | 55,56% | +7,08% | 3,11 | 2,52 | 3 | +2 807,63 EUR | +2,66% | -2,88% |
| 2025Q2 | 10 | 3/7 | 30,0% | +1,97% | 1,30 | 2,98 | 7 | +948,53 EUR | +1,42% | -4,68% |
| 2025Q3 | 4 | 2/2 | 50,0% | +3,63% | 2,40 | 2,34 | 1 | +664,01 EUR | +0,10% | -2,28% |
| 2025Q4 | 8 | 7/1 | 87,5% | +9,73% | 15,55 | 2,15 | 0 | +3 465,58 EUR | +3,28% | -1,59% |
| 2026Q1 | 7 | 2/5 | 28,57% | -0,37% | 0,94 | 2,35 | 4 | -102,80 EUR | +2,27% | -6,60% |
| 2026Q2 | 7 | 6/1 | 85,71% | +11,68% | 9,62 | 1,61 | 1 | +3 662,99 EUR | +0,76% | -9,85% |
| 2026Q3* | 1 | 1/0 | 100% | +179,45% | n/a | n/a | 0 | +8 058,62 EUR | -1,95% | -2,43% |

`*` 2026Q3 est une période partielle et ne contient qu'un trade clôturé. Ce trade, AL2SI.PA, produit +179,45% net et un MFE de +448,44%. Il doit être traité comme un **outlier de très faible effectif** et ne pas être utilisé seul pour conclure à une amélioration structurelle du modèle.

## 8. Pourquoi P&L clôturé et rendement NAV peuvent avoir des signes différents

Les colonnes ne mesurent pas la même chose :

- `pnl_net_eur` additionne uniquement le résultat des trades dont la sortie tombe dans la période;
- `rendement_portefeuille_pct` mesure la variation de la NAV entre le premier et le dernier point de la période et incorpore donc la valorisation des positions encore ouvertes.

Exemple : une période peut enregistrer deux sorties perdantes tout en terminant avec une NAV supérieure grâce à des positions encore ouvertes en plus-value latente.

Il ne faut donc ni additionner les rendements trimestriels, ni interpréter une divergence de signe comme une incohérence du moteur.

## 9. Meilleur modèle à retenir à ce stade

### Modèle retenu : BASELINE technique

Motifs :

1. il est le seul modèle dont tous les critères sont réellement disponibles sur l'ensemble des 4 380 signaux;
2. les variantes consensus/objectif sont strictement non évaluables avec l'historique PIT actuellement certifié;
3. aucune variante enrichie n'a démontré de réduction des stops, des faux positifs, du drawdown ou d'amélioration du PF/RR;
4. utiliser une valeur de consensus actuelle pour les anciens trades violerait Audit 73 et créerait un look-ahead;
5. la stabilité longue de BASELINE reste insuffisante pour promouvoir ce modèle comme solution finale optimisée.

### Statut des variantes consensus

**À conserver en recherche / forward validation**, pas à supprimer.

Leur intérêt doit être réévalué lorsque l'historique accumulé permettra un échantillon PIT réellement couvert. La collecte Boursorama/Finnhub doit donc continuer à être conservée append-only.

## 10. Conclusion métier

Le résultat longitudinal change l'interprétation du modèle : le cumul +118,18% paraît élevé, mais il est obtenu sur plus de seize ans et ne constitue pas une performance annuelle suffisante. Le PF 1,55, le RR 2,48, le taux de stops 55,76% et les ruptures de performance de 2011, 2018 et 2022 montrent que la sélection actuelle reste trop dépendante du régime.

Le stock-picking technique possède une espérance globale positive, mais **la preuve d'une surperformance durable et suffisamment stable n'est pas acquise**.

Les prochaines optimisations doivent donc viser en priorité la robustesse aux années défavorables et la réduction des faux positifs/stops, sans retuner sur le holdout 2023-2026. Les critères consensus ne pourront être jugés qu'en forward ou sur un historique PIT certifié suffisamment long.
