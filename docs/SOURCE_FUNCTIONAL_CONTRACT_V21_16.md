# Contrat fonctionnel des sources V21.16

## Objet

Ce document décrit la couche de confirmation post-sélection de PEA Analyzer. Le contrat canonique exécutable reste `config/SOURCE_FUNCTIONAL_CONTRACT_V21_16.json` (version V21.16.6).

Architecture obligatoire :

**scoring interne gouverné → présélection → enrichissement Boursorama prioritaire + timing Investing multi-horizon → gate de préparation entrée/CI**.

Boursorama et Investing ne sont pas des pondérations supplémentaires et ne peuvent pas modifier les scores, décisions internes, poids, seuils ou univers.

## Périmètre

La couche source traite au maximum 40 ISIN uniques déjà présélectionnés parmi les statuts gouvernés : BUY_CANDIDATE, WATCH, REVIEW, WATCH_NOT_TOP2, SHADOW_CANDIDATE, T1_STARTER_25_SHADOW, T1_WATCH_SHADOW et T2_CONFIRM_75_SHADOW.

T1/T2 restent strictement **ACTION TCT uniquement** :

- T1_STARTER_25_SHADOW et T1_WATCH_SHADOW = surveillance uniquement ;
- T2_CONFIRM_75_SHADOW = candidat de décision-support d'entrée, toujours SHADOW ;
- aucun T1/T2 ne peut devenir un BUY de production par la couche source.

Les horizons actifs du contrat sont TCT, CT et MT pour Actions/ETF lorsque le module correspondant existe. Le mapping Investing est fixe : TCT → Daily, CT → Weekly, MT → Monthly.

## Propriété réseau

Une seule couche possède le live par cadence :

- quotidien TCT/CT : `LIVE_IF_DUE` ;
- Comité hebdomadaire : `LIVE_IF_DUE` ;
- Action MT interne : `CACHE_ONLY` ;
- ETF MT interne : `CACHE_ONLY` ;
- restitution CI : `READ_ONLY_NO_EXTERNAL_CALLS`.

Le CI ne déclenche donc jamais une collecte externe pour compléter sa propre présentation.

## Boursorama

Boursorama est la fiche de contexte prioritaire des instruments sélectionnés. Le scraping quotidien de l'univers complet est interdit.

Politique de fraîcheur :

- dynamique : TTL 8 h ;
- performance : TTL 72 h ;
- deep : TTL 336 h ;
- retry après échec : 2 h, uniquement pour supprimer des retries trop rapprochés, jamais pour prolonger artificiellement la fraîcheur des données ;
- budget : 40 instruments ;
- départs de requêtes : 1 seconde minimum ;
- inflight fournisseur : 4 maximum.

Action prête Boursorama : consensus + nombre d'analystes + au moins objectif médian ou potentiel. Les familles de contexte couvrent notamment cotation/volume/capitalisation/secteur/PEA, performances, consensus/révisions, estimations et fondamentaux lorsque publiquement disponibles.

ETF prêt Boursorama : au moins 3 champs présents parmi AUM, catégorie Morningstar, réplication, frais de gestion, classe d'actifs et zone géographique. Les données structurelles publiques disponibles complètent également société de gestion, politique de distribution et indicateurs de risque.

Aucun HTML brut n'est persisté.

## Investing

Investing constitue uniquement le gate de timing technique post-sélection. Les pages publiques instrument/technical sont utilisées et l'identité instrument doit être validée afin d'éviter les confusions de cotation ou d'ADR.

États autorisés : STRONG_SELL, SELL, NEUTRAL, BUY, STRONG_BUY. L'état requis pour confirmer le timing est **STRONG_BUY** sur l'horizon correspondant.

Politique de fraîcheur :

- TTL : 6 h ;
- fraîcheur maximale CI : 12 h ;
- budget de refresh : 40 ;
- maximum 8 nouvelles résolutions d'URL non mappées par run ;
- cache négatif de résolution temporaire 24 h, jamais blacklist permanent ;
- cooldown d'échec technique 2 h ;
- départs de requêtes : 1 seconde minimum ;
- 4 workers maximum.

L'allocation des nouvelles résolutions respecte la priorité de présélection : BUY avant T2, puis T1 starter, shadow, WATCH, T1 watch, WATCH_NOT_TOP2 et REVIEW. Les mappings/caches déjà connus ne consomment pas un slot de découverte.

## États du gate

Les états principaux sont :

- `FULLY_VALIDATED` : BUY interne + Boursorama prêt + Investing STRONG_BUY sur l'horizon requis + fraîcheur compatible CI ;
- `TIMING_WAIT` : contexte disponible mais timing Investing non confirmé ;
- `BOURSORAMA_INCOMPLETE` : fiche prioritaire insuffisante ;
- `SOURCES_INCOMPLETE` : données/fraîcheur insuffisantes.

Pour un T2 exact, la source-ready exige T2_CONFIRM_75_SHADOW + Boursorama prêt + Investing Daily STRONG_BUY + fraîcheur conforme. Cela autorise seulement la couche ACTION de décision-support SHADOW ; cela ne crée jamais un BUY de production.

WATCH, REVIEW et T1 sont restitués mais ne peuvent pas porter l'étiquette de recommandation BUY totalement validée.

## Gouvernance

Invariants :

- post-sélection uniquement ;
- influence sur score = 0 ;
- aucun changement de décision interne ;
- aucun changement de poids ou seuil ;
- aucune imputation neutre en cas de donnée manquante ;
- N/A explicite + audit d'échec ;
- aucun ordre réel ;
- suppression silencieuse d'une fonction source interdite.

La couche source peut retarder la **préparation d'entrée/CI** lorsqu'une confirmation manque ; elle ne réécrit jamais le raisonnement financier qui a produit la présélection.

## Optimisation runtime sans perte fonctionnelle

Les caches Boursorama/Investing peuvent être préchauffés sur les sélections précédentes pendant des collectes d'un autre fournisseur. Cette préchauffe reste spéculative et non bloquante. Le gate courant est toujours exécuté ensuite et complète les nouveaux candidats ; aucun candidat courant n'est exclu parce qu'il n'existait pas dans le seed précédent.

Le préchauffage ne doit jamais créer deux écrivains simultanés du même cache source : les orchestrateurs rejoignent la tâche de préchauffe avant le gate courant.
