# V21.17 — résultat de la preuve Euronext et OHLCV réelle

## Qualification Euronext

Population source : 157 Actions du run réel V21.13 `32379163874`, toutes initialement `START_AFTER_ANCHOR_UNRESOLVED`.

| Statut | Nombre |
|---|---:|
| ISIN demandés | 157 |
| Preuves A exact-ISIN acceptées | 6 |
| Quarantaines fail-closed | 4 |
| Non résolus | 147 |

La première preuve réseau est archivée dans le run GitHub Actions `32414686922`, artifact digest `sha256:db1e57e2219f2b879b64e50665ade350f25c5e2653e270c3182894d2bb1783a1`. Elle est la source du référentiel figé `config/V21_17_ACTION_LISTING_EVIDENCE_A.csv`.

## Preuve d'intégration sur OHLCV réel

Run final d'intégration : `32447847895` sur le head `7e61f77391b5e2da43a35fdd50c544ae13101f9e`, testé par GitHub sur le merge ref avec le `main` contenant V21.16.

Artifact final : `V21_17_REAL_ACTION_LISTING_32447847895`, digest `sha256:945852ace613777217afe2d0725a3b826c10a7b1414993284bf9da2867c27772`.

Résultat :

- cache OHLCV réel restauré : 18 fichiers parquet Actions ;
- 6/6 preuves A retrouvées dans l'audit OHLCV ;
- 6/6 classées `POST_ANCHOR_INCEPTION_CONFIRMED` ;
- `primary_calibration_eligible=true` : 0/6 ;
- `PRIMARY_FULL_FROM_ANCHOR` : 0/6 ;
- historique synthétique créé : non ;
- gate de calibration modifié : non ;
- régressions V21.17 + OHLCV : SUCCESS.

La CI générale du même head a également passé compilation, Ruff, audit statique, intégrité master, gouvernance et la suite pytest complète. L'audit Action Identity Hydration est SUCCESS.

## Interprétation

Les six preuves officielles expliquent correctement pourquoi ces six séries de cours commencent après le 01/01/2023. Elles ne rendent pas ces instruments éligibles à une calibration nécessitant un historique complet depuis l'ancre. Les 147 non résolus et les quatre quarantaines conservent leur traitement fail-closed.

Ce résultat ne mesure aucune performance de modèle et n'autorise aucune modification de pondération, seuil, score, Entry/Exit, stop, sizing, holdout ou scope T1/T2.
