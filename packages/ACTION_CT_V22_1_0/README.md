# Package complet Actions CT V22.1.0

Ce package regroupe le module Actions CT actualisé et ses dépendances directes de gouvernance, scoring, contexte, tests, documentation et workflows.

## Version active du challenger

`ACTION_CT_V22.1.0_CONTEXT_ENRICHED_SHADOW`

Le module V22.1 conserve V22.0 comme parent technique et la baseline Actions CT V21.0 comme comparateur de référence. Il ne remplace pas automatiquement la baseline.

## Améliorations intégrées

- horizon CT explicite de 10 à 60 séances ;
- confluence daily/weekly ;
- tendances 20/50/120 ;
- momentum 10/20/60 et accélération ;
- breakout 55/120, mémoire de cassure, retest et failed breakout ;
- volume, RVOL, accélération volume, turnover et liquidité ;
- rotation sectorielle reconstruite à chaque run ;
- catch-up action/secteur ;
- force relative cross-sectionnelle 1m/3m/6m ;
- Morningstar Actions et potentiel cible observés via les features gouvernées existantes ;
- consensus, révisions et catalyseurs ;
- thème et macro lorsqu'ils sont réellement observés et suffisamment documentés ;
- warning secteur/thème prometteur mais survalorisé ;
- risque résultats à moins de deux jours ;
- risque d'entrée et de sortie séparés ;
- confirmation de sortie entre séances distinctes ;
- invalidation structurelle de recherche sans stop arbitraire ;
- ledger PIT immuable avec SHA-256 ;
- outcomes 10/20/40 séances depuis l'open suivant le snapshot ;
- comparaison forward-PIT contre la baseline CT V21.0 ;
- A/B possible V22.0 vs V22.1 ;
- restitution Android ;
- CI dédiée et test end-to-end.

## Interdictions maintenues

- T1/T2 restent exclusivement TCT ;
- aucun intraday / 5m ;
- aucun take-profit fixe ;
- aucun stop-loss fixe promu ;
- aucun ordre réel ;
- aucun backfill artificiel de snapshots historiques ;
- aucune promotion automatique, même après réussite des gates de recherche.

## Construction du ZIP

```bash
python scripts/build_action_ct_package_v22_1.py --output dist/ACTION_CT_V22_1_0_COMPLETE.zip
```

La CI `Action CT V22 validation` construit également automatiquement ce ZIP et le publie comme artefact GitHub `ACTION_CT_V22_1_0_COMPLETE`.

## Fichiers

Le contenu exhaustif est défini dans `packages/ACTION_CT_V22_1_0/MANIFEST.json`.
