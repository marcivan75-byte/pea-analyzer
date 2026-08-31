# HEBDO AT META — process parallèle gouverné

Branche dédiée : `hebdo-at-meta`.

## Principe

HEBDO AT META reste strictement séparé de HEBDO AT CHAT. Les fichiers hérités de CHAT servent de base technique, mais la chaîne officielle Meta est `v182/hebdo/hebdo_at_meta.py`.

## Ordre officiel des étages

1. **PIT / données** : données disponibles au plus tard T-1 selon `v182/audit/pit_loader.py`. Toute donnée critique manquante doit bloquer la sélection (`BLOCK_DATA_META`).
2. **Préfiltre faux positifs** : `false_positive_filter.py` élimine uniquement les cas les plus dégradés et conserve le mécanisme de sauvegarde des forts momentum sectoriels.
3. **Risque MAE / stop -9 %** : `mae_predictor.py` produit `prob_stop_9` et `EXCLU_MAE`. Dans Meta V1, ce flag est informatif pour l'EV ; il n'est pas un hard gate automatique.
4. **Meta-labeling** : `meta_labeler.py` produit `prob_meta`. L'entraînement est refusé si l'échantillon ou les classes sont insuffisants ; en absence de modèle entraîné, `prob_meta=0.5` et le statut reste explicite.
5. **Expected Value** : `expected_value_ranker.py` calcule `EV_net` et affecte `TCT`, `CT_WATCH`, `EXCLU` par quantiles adaptatifs.
6. **Préopen** : `preopen_enricher.py` reste un enrichissement d'exécution sur les seuls titres présélectionnés ; il ne doit pas élargir l'univers.
7. **Confirmation J+1** : `confirmation_entry.py` confirme, met en attente ou rejette l'entrée après la barre suivante. Les cas sans barre J+1 restent `BLOCK_DATA_NEXT_BAR` / attente, jamais confirmés artificiellement.
8. **Gestion post-entrée** : `fp_early_exit.py` gère stop final, fail-fast J2, faiblesse relative secteur, capitulation, momentum mort, break-even et time decay.
9. **Backtest / gouvernance** : `v21_8_1_backtest_B_v2.py`, IC/Lasso et tests servent à recalibrer et valider les paramètres sans look-ahead.

## Règles de gouvernance

- Aucun ordre réel n'est activé par HEBDO AT META V1.
- Les données synthétiques ne sont pas autorisées dans le runner officiel.
- Les modules ML ne peuvent pas être promus sans échantillon suffisant et validation OOS/PIT.
- Toute correction Meta reste sur `hebdo-at-meta` tant qu'une décision explicite de promotion n'est pas prise.
- Le workflow `.github/workflows/hebdo_at_meta.yml` compile les modules et exécute les tests Meta à chaque modification de la branche.

## Livrables officiels du runner

- `outputs/hebdo_meta/HEBDO_AT_META_RANKED.csv`
- `outputs/hebdo_meta/HEBDO_AT_META_SUMMARY.json`

Le résumé doit publier au minimum le nombre initial, le nombre après préfiltre, les volumes TCT/CT_WATCH/EXCLU, le statut du modèle Meta et `real_orders_enabled=false`.
