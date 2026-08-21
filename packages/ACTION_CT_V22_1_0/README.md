# Package complet Actions CT V22.1.1

Ce package regroupe le module Actions CT V22.1.0 et son patch runtime V22.1.1 avec les dépendances directes de gouvernance, scoring, contexte, tests, documentation et workflows.

## Versions

Décision challenger : `ACTION_CT_V22.1.0_CONTEXT_ENRICHED_SHADOW`

Patch runtime : `ACTION_CT_V22.1.1_PERFORMANCE_OBSERVABILITY_PATCH`

Le patch runtime ne change ni les poids enregistrés, ni l'epoch PIT `ACTION_CT_V22.1.0_ONLY`, ni la baseline Actions CT V21.0. Il améliore l'exécution, les diagnostics et la robustesse sans promotion automatique.

## Améliorations intégrées

- horizon CT explicite de 10 à 60 séances ;
- confluence daily/weekly ;
- tendances 20/50/120 ;
- momentum 10/20/60 et accélération ;
- breakout 55/120, mémoire de cassure, retest et failed breakout ;
- volume, RVOL, accélération volume, turnover et liquidité ;
- rotation sectorielle avec recovery gate paramétré ;
- HHI de concentration de la rotation ;
- catch-up action/secteur ;
- force relative cross-sectionnelle 1m/3m/6m vectorisée ;
- Morningstar Actions et potentiel cible observés via les features gouvernées existantes ;
- consensus, révisions et catalyseurs ;
- thème et macro lorsqu'ils sont réellement observés et suffisamment documentés ;
- warning secteur/thème prometteur mais survalorisé ;
- séparation diagnostique valuation risk / event risk ;
- score de risque asymétrique 20 séances ;
- diagnostic de qualité de liquidité ;
- context richness et couverture par champ ;
- validation explicite du schéma master ;
- batch OHLCV + calcul parallèle configurable pour les grands univers ;
- timers par étape, distributions d'états et coverage ;
- divergence V21/V22.1 et comparaison V22.0/V22.1 ;
- erreurs détaillées dans un audit séparé ;
- confirmation de sortie entre séances distinctes ;
- invalidation structurelle de recherche sans stop arbitraire ;
- ledger PIT immuable avec SHA-256 ;
- outcomes 10/20/40 séances depuis l'open suivant le snapshot ;
- restitution Android enrichie ;
- CI dédiée, tests de performance et test end-to-end.

## Tuning volontairement différé

La hausse des poids `quality_target` et `theme_macro` est pré-enregistrée dans `entry_weights_sensitivity`, mais reste désactivée. Toute activation doit ouvrir un nouvel epoch challenger et être validée PIT/holdout.

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
python scripts/build_action_ct_package_v22_1.py --output dist/ACTION_CT_V22_1_1_COMPLETE.zip
```

La CI `Action CT V22 validation` construit automatiquement ce ZIP et le publie comme artefact GitHub `ACTION_CT_V22_1_1_COMPLETE`.

## Fichiers

Le contenu exhaustif est défini dans `packages/ACTION_CT_V22_1_0/MANIFEST.json`.
