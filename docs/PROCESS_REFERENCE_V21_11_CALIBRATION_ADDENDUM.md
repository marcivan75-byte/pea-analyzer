# Addendum au process de référence — V21.11 Calibration

Cet addendum complète `PROCESS_REFERENCE_V21_8_1_FINAL.md` sans modifier les décisions de production, pondérations, seuils, stops ou règles d'entrée/sortie.

À compter de V21.11, toute **nouvelle calibration ordinaire** de paramètres doit respecter `config/CALIBRATION_WINDOWS_V21_11.json` et `src/v182/backtest/calibration_windows.py`.

Règles normatives :

1. Base principale : **01/01/2023 → date PIT du run**, poids normal.
2. Au 20/08/2026 : **44 mois calendaires touchés**.
3. Jusqu'au 31/12/2027 : fenêtre expansive post-COVID.
4. À partir du 01/01/2028 : **60 mois glissants**.
5. Bibliothèque de stress : **01/01/2020–31/12/2022**, poids de calibration **0**.
6. Le stress set ne peut optimiser ni poids ni seuils ordinaires et ne peut servir à un retuning ex post.
7. Le stress set peut rejeter une variante fragile ou déclencher une revue des protections, stops, drawdowns et changements de régime.
8. PIT, anti-look-ahead, walk-forward/OOS et holdouts propres à chaque module restent obligatoires.
9. Les historiques et preuves legacy restent conservés pour traçabilité ; ils ne deviennent pas automatiquement des données de recalibration V21.11.
10. Aucune donnée manquante n'est imputée pour augmenter artificiellement l'échantillon.

Séquence cible :

`CALIBRATION 2023+ → WALK-FORWARD/OOS → HOLDOUT VERROUILLÉ → STRESS 2020–2022 → PROMOTION`

Le document détaillé est `docs/CALIBRATION_WINDOWS_V21_11.md`.
