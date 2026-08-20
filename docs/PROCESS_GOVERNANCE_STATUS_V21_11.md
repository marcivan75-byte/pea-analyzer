# PEA Analyzer — Statut de gouvernance V21.11 Calibration

Date de référence : 20/08/2026

## Statut technique

L'implémentation V21.11 de gouvernance des fenêtres historiques a passé les validations de code, intégrité, référentiel et suite pytest sur la PR #105. L'état d'intégration GitHub (draft/ready/merged) est porté par la PR et reste la source de vérité pour la clôture WIP.

## Architecture normative

- calibration ordinaire : **01/01/2023 → date PIT du run** ;
- au 20/08/2026 : **44 mois calendaires touchés** ;
- jusqu'au 31/12/2027 : fenêtre expansive post-COVID ;
- à partir du 01/01/2028 : **60 mois glissants** ;
- bibliothèque de stress : **01/01/2020–31/12/2022** ;
- influence de la stress library dans la calibration ordinaire : **0** ;
- optimisation/retuning de poids ou seuils sur le stress set : **interdits** ;
- stress autorisé comme contrôle de robustesse, rejet de fragilité et revue des protections ;
- PIT, anti-look-ahead, OOS et holdouts propres à chaque module : **inchangés et obligatoires**.

## Non-régression

Ce chantier ne change aucun poids, seuil, score, stop, règle d'entrée/sortie, univers ou ordre de production. Les preuves historiques legacy des modèles gelés restent conservées pour traçabilité mais ne deviennent pas automatiquement une nouvelle calibration V21.11.

Références exécutables :

- `config/CALIBRATION_WINDOWS_V21_11.json` ;
- `src/v182/backtest/calibration_windows.py` ;
- `tests/test_calibration_windows_v21_11.py` ;
- `docs/CALIBRATION_WINDOWS_V21_11.md` ;
- `docs/PROCESS_REFERENCE_V21_11_CALIBRATION_ADDENDUM.md`.

La clôture WIP exige en plus un head final propre, un CI final vert et une fusion gouvernée de la PR correspondante.
