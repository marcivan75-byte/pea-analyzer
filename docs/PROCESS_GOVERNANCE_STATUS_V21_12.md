# PEA Analyzer — Statut de gouvernance V21.12 Calibration

Date de référence : 20/08/2026

## Statut technique

V21.12 a introduit la politique centrale de fenêtres historiques et V21.12.1 a corrigé la borne de fin calendaire du stress set. V21.12.2 ajoute un **gate runtime** afin que cette politique soit contrôlée pendant l'audit de gouvernance du Committee hebdomadaire.

Ce gate ne recalibre aucun modèle et ne remplace aucun protocole OOS/holdout spécifique. Il vérifie la politique puis publie `outputs/audit/CALIBRATION_GOVERNANCE_V21_12.json`.

## Architecture normative

- calibration ordinaire : **01/01/2023 → date PIT du run** ;
- au 20/08/2026 : **44 mois calendaires touchés** ;
- jusqu'au 31/12/2027 : fenêtre expansive post-COVID ;
- à partir du 01/01/2028 : **60 mois glissants** ;
- bibliothèque de stress : **01/01/2020–31/12/2022**, journée finale entière incluse ;
- influence de la stress library dans la calibration ordinaire : **0** ;
- optimisation/retuning de poids ou seuils sur le stress set : **interdits** ;
- stress autorisé comme contrôle de robustesse, rejet de fragilité et revue des protections ;
- PIT, anti-look-ahead, OOS et holdouts propres à chaque module : **inchangés et obligatoires**.

## Gate runtime V21.12.2

`src/v182/reporting/calibration_governance_audit.py` échoue en cas de dérive de l'un des invariants gelés :

- ancre principale différente du 01/01/2023 ;
- activation rolling différente du 01/01/2028 ;
- rolling différent de 60 mois ;
- poids de calibration stress différent de 0 ;
- optimisation ou retuning autorisé sur le stress set ;
- période de stress différente de 2020–2022 ;
- PIT ou anti-look-ahead désactivé ;
- verrouillage des holdouts spécifiques supprimé.

`criteria_governance_audit` appelle ce gate. Le workflow Committee hebdomadaire exécute déjà `python -m v182.reporting.criteria_governance_audit`, ce qui rend le contrôle observable dans le process courant sans modifier les décisions.

Les futures fonctions de calibration ordinaire doivent utiliser les helpers fail-closed de `src/v182/backtest/calibration_windows.py`. En revanche, un module disposant déjà d'un protocole PIT/OOS pré-enregistré conserve ce protocole ; V21.12 ne doit pas réécrire ses périodes a posteriori.

## Non-régression et compatibilité

Ce chantier ne change aucun poids, seuil, score, stop, règle d'entrée/sortie, univers ou ordre de production. Les preuves historiques legacy des modèles gelés restent conservées pour traçabilité mais ne deviennent pas automatiquement une nouvelle calibration V21.12.

V21.11 ETP Satellite Or/Crypto reste `SHADOW_ONLY / CONTEXT_ONLY`. Toute future calibration de cette voie satellite devra respecter V21.12 et son propre protocole PIT/OOS avant promotion.

Références exécutables :

- `config/CALIBRATION_WINDOWS_V21_12.json` ;
- `src/v182/backtest/calibration_windows.py` ;
- `src/v182/reporting/calibration_governance_audit.py` ;
- `src/v182/reporting/criteria_governance_audit.py` ;
- `tests/test_calibration_windows_v21_12.py` ;
- `tests/test_calibration_governance_runtime_v21_12.py` ;
- `tests/test_calibration_governance_process_wiring_v21_12.py` ;
- `docs/CALIBRATION_WINDOWS_V21_12.md` ;
- `docs/PROCESS_REFERENCE_V21_12_CALIBRATION_ADDENDUM.md`.

La clôture V21.12.2 exige un head final propre, un CI complet vert et une fusion gouvernée de la PR correspondante.
