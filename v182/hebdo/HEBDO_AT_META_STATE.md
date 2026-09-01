# Hebdo AT Meta — état de référence

Dernière consolidation: 2026-09-01
Branche de référence: `hebdo-at-meta`

## Règle de gouvernance

Ce document est la porte d'entrée de l'état courant de Hebdo AT Meta.

Lorsqu'une directive ancienne provenant d'un chat, d'une note ou d'un artefact est rappelée, elle ne doit **jamais** être réintroduite automatiquement dans le process. Elle doit d'abord être confrontée aux éléments plus récents selon l'ordre de priorité suivant:

1. code et workflow présents au HEAD de `hebdo-at-meta`;
2. résultats des runs GitHub Actions réussis correspondant au HEAD ou à ses ancêtres récents;
3. décisions Work les plus récentes récupérables et datées;
4. artefacts publiés et leurs contrats de validation;
5. seulement ensuite, directives de chats plus anciennes.

En cas de conflit, la décision la plus récente et explicitement validée prévaut. Une règle ancienne non retrouvée dans le process courant est classée `LEGACY_OR_UNRESOLVED`, jamais `ACTIVE` par défaut.

## État courant vérifié

### HEAD

- Commit: `98bbf4b3508f2239e6f34680f9dc309d4732382c`
- Message: `ci(pre2023): build governed Yahoo development corpus`
- Date: 2026-09-01T19:35:12Z

### TABPORT enrichi

Publication validée:

- workflow: `TABPORT enriched publication`
- run: `33549108644`
- commit de publication: `68bcad1cefd60816ad0ab81df58ba5c48b549523`
- statut: `success`
- artefact: `TABPORT-ENRICHI-33549108644`

Chaîne publiée au moment de cette validation:

`B_V2 -> META -> confirmation J+1 -> TABPORT stop fixe 0.9`

Le workflow publie et valide notamment:

- baseline TABPORT enrichi;
- matrice anti-faux-positifs;
- attribution appariée;
- walk-forward;
- stop-risk overlay;
- flat-EV tie-break;
- convexité calibrée;
- convexité continue;
- décision de stop gouvernée;
- ledger, NAV, résultats trimestriels et annuels.

Résultats globaux publiés de ce run:

- 88 trades;
- 41 gains / 47 pertes ou faux positifs;
- win rate 46.59%;
- gain moyen +21.87%;
- perte moyenne -9.63%;
- espérance +5.05% par trade;
- PF 1.99;
- RR/payoff 2.27;
- P&L net +19 845 EUR;
- capital 65 000 EUR -> 84 845 EUR;
- performance +30.53%;
- drawdown maximal -13.08%.

La stabilité interannuelle reste un point de vigilance, notamment 2025.

### Gouvernance historique / PRE2023

Architecture de recherche retenue:

- 2010-2022: développement et backtests;
- 2023-2026: holdout / OOS uniquement;
- interdiction de retuner sur 2023-2026;
- rapports longitudinaux autorisés uniquement avec segmentation explicite;
- PIT / anti-look-ahead obligatoires;
- comportement fail-closed en cas de donnée historique insuffisante ou invalide.

Le corpus Yahoo PRE2023 gouverné a été construit et validé:

- workflow: `PRE2023 Yahoo development corpus`;
- run: `33550367844`;
- commit: `98bbf4b3508f2239e6f34680f9dc309d4732382c`;
- statut: `success`.

Le corpus brut 2010-2022 exclut fail-closed les lignes OHLCV invalides/incomplètes et ne doit pas injecter de données >=2023 dans le développement.

## Contraintes Work récentes à préserver

- PIT T-1 22h Paris pour les données utilisées dans la décision;
- fail-closed si historique requis incomplet;
- pas de TCT exploitable sans Meta temporel/OOS dûment entraîné;
- aucun résultat historique ne doit être publié à partir de snapshots/features non réellement disponibles à la date simulée;
- les données réelles du cache historique doivent être privilégiées aux reconstructions artificielles.

Paramètres TABPORT implémentés au stade de validation du 1er septembre 2026:

- capital initial: 65 000 EUR;
- 12 lignes;
- 4 500 EUR par ligne;
- 5 entrées/mois;
- 40/an;
- frais 0.20% par côté;
- slippage 0.10%;
- stop fixe -9%;
- maturité 126 séances.

## Règles anciennes / non automatiquement actives

Les directives antérieures telles que:

- objectif médian >= +20%;
- consensus BUY/STRONG_BUY;
- bonus de révision de consensus;
- nombre d'analystes comme mesure de confiance;
- WAIT en cas de données fondamentales absentes;
- collecte Boursorama/Finnhub concentrée sur les finalistes Meta;
- chaîne J -> J+1 -> filtre fondamental -> entrée J+2;

ne doivent **pas** être considérées comme actives uniquement parce qu'elles apparaissent dans un ancien chat. Leur statut est `LEGACY_OR_UNRESOLVED` tant qu'une décision Work plus récente, un module au HEAD, un test ou un workflow validé ne démontre pas leur promotion dans la chaîne active.

Cela n'implique pas qu'elles soient rejetées: cela interdit seulement leur réintroduction rétrograde sans preuve de leur statut le plus récent.

## Discipline de mise à jour

À chaque reprise du projet:

1. relire le HEAD `hebdo-at-meta`;
2. vérifier les derniers runs Actions;
3. récupérer les décisions Work récentes disponibles;
4. comparer leur chronologie;
5. mettre à jour ce document si l'état de référence change;
6. seulement ensuite proposer ou appliquer une modification du process.

Aucun ancien extrait de conversation ne doit, à lui seul, remplacer l'état courant vérifié.
