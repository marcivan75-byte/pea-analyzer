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

### HEAD technique de la branche avant cette mise à jour documentaire

- Commit: `fdf995a5857e1d736b39bb82c60925aed411c5ec`
- Message: `ci(meta): validate governed 2010-2026 access`
- Date: 2026-09-01T19:49:50Z
- Nature: validation technique/gouvernance de l'accès aux historiques 2010-2026; ce commit ne constitue pas à lui seul une nouvelle version métier postérieure à Audit 73.

### Référence fonctionnelle Work — Audit 73

La dernière référence fonctionnelle officielle communiquée par Work pour `HEBDO AT META` est **Audit 73 intégré**.

Référence communiquée:

- commit court annoncé: `f456281`;
- message annoncé: `audit73(hebdo-meta): preserve Boursorama consensus history`;
- statut fonctionnel: **référence officielle Audit 73 à préserver**;
- particularité: conservation de l'historique du consensus Boursorama.

Le SHA court `f456281` n'est actuellement pas résolu par l'historique GitHub accessible de `marcivan75-byte/pea-analyzer`. Cette absence de résolution ne doit pas conduire à déclasser Audit 73 ni à effacer sa décision fonctionnelle. Elle doit être traitée comme une anomalie de traçabilité Git à investiguer.

Les commits techniques ultérieurs présents sur `hebdo-at-meta` (PRE2023, accès gouverné 2010-2026, CI) **ne sont pas assimilés à un Audit 74** sauf décision fonctionnelle Work explicite.

### Statut Boursorama après Audit 73

Audit 73 impose de **préserver l'historique du consensus Boursorama**. En conséquence:

- l'historique Boursorama n'est plus classé `LEGACY_OR_UNRESOLVED` en tant que donnée/historique à conserver;
- sa collecte, sa conservation chronologique et sa traçabilité PIT doivent être préservées lorsque disponibles;
- aucune observation historique ne doit être remplacée rétroactivement par la dernière valeur connue;
- l'absence d'une valeur historique ne doit pas être comblée avec une valeur future;
- la conservation de cet historique ne signifie pas automatiquement que `consensus BUY/STRONG_BUY`, le potentiel, les révisions ou le nombre d'analystes sont des **critères de scoring actifs** du moteur Meta courant.

Le moteur `v182/hebdo/hebdo_at_meta.py` vérifié au HEAD ne référence pas directement une colonne Boursorama/consensus parmi ses colonnes obligatoires. Toute promotion du consensus Boursorama en filtre, bonus, pondération ou critère décisionnel actif doit donc être démontrée par le code/tests/workflow correspondant ou par une décision Work explicite distincte.

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

Le HEAD technique `fdf995a5857e1d736b39bb82c60925aed411c5ec` ajoute en outre la validation de l'accès gouverné 2010-2026 avec séparation développement 2010-2022 / holdout 2023-2026 et blocage du fit sur le holdout.

## Contraintes Work récentes à préserver

- Audit 73 est la référence fonctionnelle officielle tant qu'un audit métier ultérieur n'est pas explicitement identifié;
- préserver l'historique du consensus Boursorama conformément à Audit 73;
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

La **conservation de l'historique Boursorama est acquise par Audit 73**. En revanche, les usages décisionnels suivants restent à distinguer de cette conservation et ne doivent pas être déclarés `ACTIVE` sans preuve technique ou décision Work explicite:

- objectif médian >= +20%;
- filtre consensus BUY/STRONG_BUY;
- bonus de révision de consensus;
- nombre d'analystes comme mesure de confiance;
- WAIT en cas de données fondamentales absentes;
- collecte Boursorama/Finnhub uniquement sur les finalistes Meta;
- chaîne J -> J+1 -> filtre fondamental -> entrée J+2.

Leur statut décisionnel est `LEGACY_OR_UNRESOLVED` tant qu'un module au HEAD, un test, un workflow validé ou une décision Work plus récente ne démontre pas leur promotion dans la chaîne active.

## Discipline de mise à jour

À chaque reprise du projet:

1. relire le HEAD `hebdo-at-meta`;
2. identifier séparément le dernier commit technique et la dernière référence fonctionnelle Work;
3. vérifier les derniers runs Actions;
4. récupérer les décisions Work récentes disponibles;
5. comparer leur chronologie;
6. préserver explicitement les acquis Audit 73, notamment l'historique Boursorama;
7. mettre à jour ce document si l'état de référence change;
8. seulement ensuite proposer ou appliquer une modification du process.

Aucun ancien extrait de conversation ne doit, à lui seul, remplacer l'état courant vérifié; inversement, un commit purement technique ultérieur ne doit pas être présenté comme une nouvelle version fonctionnelle sans preuve.
