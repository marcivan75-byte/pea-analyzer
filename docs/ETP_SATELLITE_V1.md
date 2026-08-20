# ETP Satellite V1 — Or / Crypto

## Objet

Créer une voie satellite distincte des 102 ETF PEA pour les ETP/ETF Or et Crypto déjà suivis par le module Fund Flows V1.

## Gouvernance

- `SHADOW_ONLY` et influence décisionnelle nulle.
- Aucun ordre réel, aucune promotion automatique, aucune pondération PEA réutilisée.
- Aucun classement commun entre PEA, Or, Crypto long et Crypto short.
- T1/T2 interdits : ils restent réservés aux Actions TCT.
- Les produits inverses/short restent dans une voie spéculative séparée.
- Les valeurs manquantes restent `DATA_INSUFFICIENT`; aucune imputation neutre.

## Sources de contexte

- Or : scores/qualité du moteur `GOLD_V1_1` lorsqu'un résultat courant est disponible, plus le score de flux produit Fund Flows V1.
- Crypto : flux ETP/ETF uniquement. Aucun moteur alpha Crypto n'est déclaré tant qu'un modèle dédié PIT/OOS n'a pas été développé et validé.
- Univers : `ETF_FUND_FLOW_EXTERNAL_UNIVERSE_V1.csv`, limité aux classes Gold/Crypto explicites. Les ETF externes génériques servent aux comparaisons de flux mais ne sont pas inclus dans la voie satellite.

## Sorties

- `outputs/etp_satellite/ETP_SATELLITE_CONTEXT_SHADOW.csv`
- `outputs/audit/ETP_SATELLITE_V1_SHADOW.json`
- `outputs/mobile/ETP_SATELLITES_SHADOW.md`

Le module est exécuté après Fund Flows dans le workflow existant. Aucun nouveau schedule GitHub n'est créé.
