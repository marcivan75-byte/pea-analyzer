# V21.16 — Preuves d'inception ETF et qualification des historiques courts

Date de référence : 20/08/2026

## Problème traité

Le run réel V21.13 a identifié 33 ETF dont l'historique Yahoo commence après le 01/01/2023. Une première observation Yahoo n'est pas une preuve de création : elle peut correspondre à une nouvelle classe, une nouvelle cotation, un changement de ticker ou un trou de fournisseur.

V21.16 ajoute une preuve d'inception attribuée par ISIN exact afin d'expliquer ces historiques courts sans fabriquer de données antérieures.

## Hiérarchie de preuve

1. `share_class_inception_date` — preuve A issue de l'émetteur, par exemple `Date de création de la classe` dans une factsheet Amundi. C'est l'autorité préférée pour déterminer l'existence de la classe correspondant à l'ISIN.
2. `listing_or_launch_date` — preuve B issue d'un profil justETF exact-ISIN lorsque la date de classe officielle n'est pas disponible.
3. `reported_first_nav_date` — contexte uniquement. Cette date peut correspondre à un compartiment ou à une lignée antérieure et ne doit jamais écraser une création de classe plus récente.

Une source qui ne contient pas l'ISIN exact est rejetée. Aucun fuzzy matching n'est autorisé.

## Persistance

Les trois champs utilisent le state existant :

`state/provenance/etf_structure/ETF_STRUCTURE_SNAPSHOT.csv`

Aucun nouveau cache, aucun nouveau cron et aucun statut de validation du merge ne sont créés. Comme les dates sont structurelles, leur TTL de replay est long, mais leur valeur reste liée au hash et à la provenance effectivement retenue.

## Classification OHLCV

Pour un historique commençant après l'ancre 01/01/2023 :

- `POST_ANCHOR_INCEPTION_CONFIRMED` : une date de classe ou de lancement gouvernée est postérieure à l'ancre et cohérente avec la première observation de marché ;
- `PRE_ANCHOR_INCEPTION_HISTORY_GAP_CONFIRMED` : la classe existait avant l'ancre, donc le démarrage tardif de l'historique est un problème de continuité/ticker/fournisseur à investiguer ;
- `START_AFTER_ANCHOR_UNRESOLVED` : aucune preuve gouvernée disponible ;
- `INCEPTION_EVIDENCE_CONFLICT_*` : la preuve est future ou postérieure à des observations de marché déjà présentes ; le cas reste bloqué.

`reported_first_nav_date` n'est jamais utilisé pour choisir l'un de ces statuts.

Le gate conflit V21.16 concerne la qualification des historiques courts. Une extension ultérieure pourra auditer systématiquement les contradictions d'inception sur les ETF disposant déjà d'une fenêtre OHLCV complète ; elle ne devra jamais promouvoir un instrument ni fabriquer des cours.

## Calibration

V21.16 ne modifie pas le gate de calibration. Seul `PRIMARY_FULL_FROM_ANCHOR` reste éligible à la calibration principale complète.

Un ETF `POST_ANCHOR_INCEPTION_CONFIRMED` est donc correctement expliqué mais reste **non éligible** à la calibration pleine fenêtre. Une éventuelle méthode dédiée aux nouveaux ETF devra faire l'objet d'un protocole PIT/OOS distinct avant promotion.

Aucun rendement pré-inception n'est créé, recopié ou imputé. La bibliothèque de stress 2020–2022 reste inaccessible aux classes créées après 2023 et son poids de calibration reste 0.

## Non-régression

V21.16 ne change :

- aucun poids ou seuil ;
- aucune règle Entry/Exit ou stop ;
- aucun holdout ;
- aucun ordre réel ;
- aucune portée T1/T2 ;
- aucune règle ETF MT de référence.

## Validation réelle du 20/08/2026

Le workflow temporaire de qualification réelle `32405246991` a exécuté la collecte sur les 102 ETF et a publié l'artefact `V21_16_REAL_ETF_INCEPTION_32405246991` (ZIP SHA-256 `8d62a054c7c72be87954147f9dc805319656b77523a5d37be2276bf23c40abe6`).

Collecte gouvernée :

- 102 ETF demandés ;
- 147 observations exact-ISIN acceptées ;
- 45 `share_class_inception_date` ;
- 57 `listing_or_launch_date` ;
- 45 `reported_first_nav_date` ;
- 90 observations de niveau A et 57 de niveau B ;
- 0 observation mise en quarantaine ;
- 0 échec de collecte sur cette passe.

Qualification des 33 historiques ETF courts :

- 29 `POST_ANCHOR_INCEPTION_CONFIRMED` ;
- 4 `PRE_ANCHOR_INCEPTION_HISTORY_GAP_CONFIRMED` ;
- 0 `START_AFTER_ANCHOR_UNRESOLVED` parmi ces 33 ETF ;
- 0 conflit d'inception ;
- aucun historique synthétique créé.

Les quatre cas pré-ancre restent des lacunes de continuité/ticker/fournisseur à traiter séparément ; leur existence antérieure ne permet pas d'inventer les cours manquants.

Le nombre d'instruments éligibles à la calibration principale est resté strictement identique avant/après l'ajout des preuves d'inception dans le run : V21.16 explique les historiques courts mais ne les promeut pas.

Le workflow réseau temporaire a été supprimé. Le head propre destiné à la fusion a obtenu Committee Master et Action Identity au vert avec leurs full suites. Les identifiants exacts du dernier head sont consignés dans la PR afin de ne pas créer un nouveau commit documentaire uniquement pour modifier des références de CI.
