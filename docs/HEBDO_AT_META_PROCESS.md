# HEBDO AT META — process parallèle gouverné

Branche dédiée : `hebdo-at-meta`.

## Principe

HEBDO AT META reste strictement séparé de HEBDO AT CHAT. Les fichiers hérités de CHAT servent de base technique, mais la chaîne officielle Meta est `v182/hebdo/hebdo_at_meta.py`.

## Ordre officiel des étages

1. **PIT / données** : données disponibles au plus tard T-1 22h Europe/Paris selon `v182/audit/pit_loader.py`. Une provenance sidecar (`available_at`/`snapshot_at`) est privilégiée et le mode strict interdit le recours au seul `mtime`. Le jour de décision est normalisé en Europe/Paris avant calcul du cutoff.
2. **Validation ligne par ligne** : ticker, prix, SMA200, volatilité, drawdown, momentum secteur et ADV doivent être présents et cohérents. Une ligne critique invalide est retirée du scoring ; si tout l'univers devient invalide, le run bloque.
3. **Préfiltre faux positifs** : `false_positive_filter.py` élimine les cas les plus dégradés. Les dates de résultats passées ne sont jamais assimilées à un événement futur.
4. **Risque MAE / stop -9 %** : `mae_predictor.py` produit `prob_stop_9` et `EXCLU_MAE`. Ce flag reste informatif pour l'EV tant qu'une validation OOS n'a pas démontré qu'un hard gate améliore le portefeuille.
5. **Meta-labeling** : `meta_labeler.py` exige une preuve d'ordre temporel (`date` ou DatetimeIndex), des features complètes et une séparation chronologique 60/20/20 train/calibration/test. En absence de modèle entraîné, `prob_meta=0.5` et aucun titre ne peut être promu TCT.
6. **Expected Value** : `expected_value_ranker.py` calcule `EV_net`. Une EV négative reste `EXCLU`. Sans modèle `TRAINED_TEMPORAL_OOS`, les meilleurs candidats sont au maximum `CT_WATCH`, avec confiance dégradée explicite.
7. **Préopen** : `preopen_enricher.py` travaille uniquement sur les titres présélectionnés, respecte `as_of_date`, calcule le vrai gap ouverture / clôture précédente et publie un statut données. Une donnée préopen absente n'est jamais remplacée silencieusement par un gap nul.
8. **Confirmation J+1** : `confirmation_entry.py` utilise la première barre chronologiquement postérieure au signal. Si plusieurs barres sont fournies sans dates, la confirmation bloque pour ambiguïté au lieu d'en choisir une arbitrairement.
9. **Gestion post-entrée** : `fp_early_exit.py` gère stop final, fail-fast J2, faiblesse relative secteur, capitulation, momentum mort, break-even et time decay. Un gap sous le stop est exécuté au prix d'ouverture défavorable, pas au stop théorique.
10. **Backtest / gouvernance** : `v21_8_1_backtest_B_v2.py` calcule MAE/MFE uniquement jusqu'à la sortie, traite les gaps sous stop de façon conservatrice et bloque les horizons 26 semaines incomplets sans stop. IC/Lasso utilise `TimeSeriesSplit` et exclut les forward labels absents au lieu de les imputer à zéro.

## Règles de gouvernance

- Aucun ordre réel n'est activé par HEBDO AT META V1.
- Les données synthétiques ne sont pas autorisées dans le runner officiel.
- Un modèle ML ne peut produire une conviction TCT qu'après validation temporelle OOS et calibration documentée.
- Les labels futurs manquants ne sont jamais imputés à zéro.
- Une barre future ou ambiguë ne peut pas être choisie implicitement.
- Un P&L 26 semaines sans stop n'est valide que si l'horizon complet est disponible.
- Les coûts et pertes de gap sont modélisés de façon conservatrice ; aucun fill optimiste au stop n'est autorisé.
- Toute correction Meta reste sur `hebdo-at-meta` tant qu'une décision explicite de promotion n'est pas prise.
- Le workflow `.github/workflows/hebdo_at_meta.yml` compile les modules et exécute les tests backtest, Meta et PIT à chaque modification de la branche.

## Livrables officiels du runner

- `outputs/hebdo_meta/HEBDO_AT_META_RANKED.csv`
- `outputs/hebdo_meta/HEBDO_AT_META_SUMMARY.json`
- `outputs/hebdo_meta/HEBDO_AT_META_BLOCK.json` en cas de blocage données

Le résumé publie au minimum l'univers initial, les lignes critiques invalides retirées, l'univers après préfiltre, les volumes TCT/CT_WATCH/EXCLU, le statut du modèle Meta et `real_orders_enabled=false`.
