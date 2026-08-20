# V21.11 — Scope de validation gelé avant résultats

Avant exécution CI, les règles suivantes sont gelées :

- aucune modification de poids ou seuil ;
- aucune modification de l'Entry/Exit V21.8 ;
- aucune ouverture de holdout ;
- aucune utilisation de T1/T2 hors ACTION TCT ;
- calibration principale ancrée au 01/01/2023 jusqu'à fin 2027 ;
- rolling 60 mois à partir du 01/01/2028 ;
- stress 2020–2022 séparé, calibration_weight=0 ;
- aucune optimisation ou retuning à partir des résultats de stress ;
- stress autorisé uniquement comme contrôle de robustesse/rejet/protection ;
- PIT et anti-look-ahead obligatoires.

Les tests V21.11 doivent démontrer la séparation des ensembles et le rejet fail-closed des lignes stress/futures dans la calibration principale.
