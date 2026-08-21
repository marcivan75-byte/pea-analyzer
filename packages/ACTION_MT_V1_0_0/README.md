# Package ACTION MT V1.0.0 SHADOW

Module gouverné de sélection moyen terme des Actions PEA. Il combine le socle de données et les garde-fous d'ACTION CT avec le classement transversal, le filtre de régime et l'abstention d'ETF MT.

## Contenu

- `src/v182/features/action_mt_v1.py` : score individuel 3 à 12 mois.
- `src/v182/decision/action_mt_decision_v1.py` : comité, régime et plafonds sectoriels.
- `src/v182/sources/action_mt_cache_v1.py` : cache OHLCV gouverné, fraîcheur et SHA-256.
- `src/v182/reporting/action_mt_shadow_run_v1.py` : run complet, observabilité, sorties et ledger PIT.
- `config/ACTION_MT_V1_0_0_SHADOW.json` : poids, seuils et verrous.
- `scripts/validate_action_mt_ci.py` : contrat décisionnel du CI.
- `tests/` : tests moteur, décision, cache, clôture locale, ledger et runner.
- `schemas/` : contrats JSON des rapports.
- `docs/` : référentiel fonctionnel et politique de cache.

## Préparation

```bash
python -m pip install -e ".[test]"
```

Le cache est local et en lecture seule pour le moteur. Chaque fichier porte le nom de l'ISIN normalisé :

```text
data/cache/actions/FR0000120271.parquet
data/cache/actions/FR0000120271.csv
```

Colonnes minimales : index date, `open`, `high`, `low`, `close`, `volume`. Le CSV peut utiliser les mêmes noms avec une casse quelconque. Le module ne télécharge jamais silencieusement une donnée manquante ou périmée.

## Exécution

```bash
python -m v182.reporting.action_mt_shadow_run_v1 \
  --master inputs/ACTIONS_MASTER.csv \
  --cache-dir data/cache/actions \
  --output-dir outputs/action_mt_v1
```

Sorties :

- `ACTION_MT_LATEST.csv` : snapshots et classement courant ;
- `ACTION_MT_PIT_LEDGER.csv` : ledger idempotent avec fingerprint ;
- `ACTION_MT_EXCLUSIONS.csv` : journal ordonné des non-sélectionnés ;
- `ACTION_MT_CACHE_MANIFEST.json` : hits, misses, fraîcheur et politique ;
- `ACTION_MT_RUN_REPORT.json` : régime, sélection et temps d'exécution ;
- `ACTION_MT_COMMITTEE.txt` : résumé compact du comité.

## Validation

```bash
python -m pytest -q tests/test_action_mt_v1.py tests/test_action_mt_runtime_v1.py
python scripts/validate_action_mt_ci.py
```

Le workflow GitHub échoue si les poids dérivent, si les protections SHADOW sont levées, si l'abstention en régime adverse ne fonctionne plus ou si l'audit obligatoire n'est pas produit.

## Sécurité et limites

- Aucun ordre réel, sizing de production ou take-profit fixe.
- Données intraday et T1/T2 interdites.
- Barre du jour ignorée avant 18:00 Europe/Paris.
- Snapshot courant interdit pour reconstruire un backtest historique.
- Données fondamentales et consensus sans date `as-of` considérées manquantes.
- Paramètres research-only ; holdout verrouillé ; promotion humaine obligatoire.

Ce package est un challenger SHADOW et non une stratégie certifiée.

