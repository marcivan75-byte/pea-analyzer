# V21.17 — preuves officielles de cotation Actions à historique court

> Depuis V21.13.7, cette preuve est un référentiel statique d'identité Actions.
> Le collecteur Euronext, le workflow temporaire et l'ensemble du processus IPO
> ont été supprimés ; aucune actualisation réseau ou évaluation IPO n'est active.

## Objet

V21.17 qualifie exclusivement les 157 Actions du run réel V21.13 (`32379163874`) qui étaient encore `START_AFTER_ANCHOR_UNRESOLVED`. La première observation Yahoo n'est jamais utilisée comme preuve de date de cotation.

## Preuve réseau réelle

Le workflow temporaire V21.17 a interrogé le showcase IPO officiel Euronext du 01/01/2023 au 20/08/2026 et a rapproché les résultats uniquement par ISIN exact.

- run GitHub Actions de preuve : `32414686922` ;
- digest de l'artifact : `sha256:db1e57e2219f2b879b64e50665ade350f25c5e2653e270c3182894d2bb1783a1` ;
- population demandée : 157 ISIN uniques ;
- preuves A acceptées : 6 ;
- quarantaines : 4 ;
- non résolus : 147.

Les 6 preuves A sont figées dans `config/V21_17_ACTION_LISTING_EVIDENCE_A.csv`. Les 4 cas ambigus sont conservés séparément dans `config/V21_17_ACTION_LISTING_QUARANTINE.csv`; ils ne sont jamais consommés comme preuve.

## Règles fail-closed

Une preuve n'est applicable que si l'ISIN correspond exactement, que `evidence_level=A`, que `validation_status=EXACT_ISIN_OFFICIAL_LISTING_DATE`, et que la date officielle est exploitable. Une date déjà présente mais différente ne doit jamais être écrasée silencieusement : le conflit bloque l'application de l'overlay.

Les 147 ISIN sans preuve Euronext et les 4 ISIN en quarantaine restent non résolus. Aucune inférence par nom, symbole, pays ou première observation de cours n'est autorisée.

## Effet sur l'audit OHLCV

La preuve officielle peut transformer un diagnostic `START_AFTER_ANCHOR_UNRESOLVED` en `POST_ANCHOR_INCEPTION_CONFIRMED` lorsqu'elle explique réellement le début tardif de la série. Cela ne crée aucune observation antérieure à la cotation.

Le gate de calibration demeure inchangé : seul `PRIMARY_FULL_FROM_ANCHOR` donne `primary_calibration_eligible=true`. Un instrument `POST_ANCHOR_INCEPTION_CONFIRMED` reste exclu de la calibration primaire.

## Gouvernance inchangée

V21.17 ne modifie aucun poids, seuil, score, décision, règle Entry/Exit, stop, sizing, holdout ou univers. Aucun historique synthétique et aucune imputation de cours ne sont créés. T1/T2 restent exclusivement dans le module Actions TCT. Yahoo reste une source de données de marché/triage et n'est pas une autorité de date de cotation.
