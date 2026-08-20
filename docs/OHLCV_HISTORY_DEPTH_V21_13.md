# V21.13 — Qualification réelle de la profondeur OHLCV

Date de référence : 20/08/2026

## Objectif

V21.13 vérifie que la fenêtre de calibration V21.12 (01/01/2023 → date PIT) existe réellement dans les caches OHLCV et mesure séparément la disponibilité de la bibliothèque de stress 2020–2022.

Aucune pondération, aucun seuil, aucun score, aucune règle Entry/Exit et aucun holdout n'est modifié.

## Reconstruction historique

Le bootstrap Yahoo n'utilise plus une fenêtre relative plus large de type `10y`. La borne gouvernée devient explicitement **`start=2020-01-01`**.

Cette architecture télécharge uniquement la profondeur nécessaire à la bibliothèque de stress 2020–2022 et à la calibration post-COVID 2023+, sans charger 2016–2019 inutilement. Le paramètre historique `period=5y` reste seulement un fallback technique pour un appel explicitement effectué avec `start=None`; le processus PEA V21.13 utilise par défaut `2020-01-01`.

La borne `bootstrap_start` fait désormais partie du manifeste de compatibilité du cache. Un cache V21.12 hérité, qui ne possède pas cette borne, est déclaré incompatible et déclenche automatiquement un `FULL_BOOTSTRAP` depuis le 01/01/2020 au premier run V21.13. Les tailles de lots historiques 100 Actions / 50 ETF sont conservées ; elles ne servent plus artificiellement à provoquer la reconstruction.

Les runs suivants redeviennent incrémentaux et conservent l'historique long. En cas de révision matérielle de prix ajustés dans une zone déjà clôturée, le batch concerné est reconstruit depuis la même borne `2020-01-01`.

`required_history_start=2020-01-01` est un invariant d'audit : si une future reconstruction ou éviction de cache ne permet plus de récupérer cette profondeur, le rapport V21.13 doit le rendre visible plutôt que considérer la bibliothèque de stress comme complète.

## Audit instrument par instrument

`src/v182/audit/ohlcv_history_depth.py` lit uniquement les observations disposant d'un `Close` numérique réel. Les lignes de padding créées par l'union des index Yahoo ne comptent jamais comme historique disponible.

Pour chaque Action/ETF :

- premier et dernier Close observés ;
- nombre de séances et de mois calendaires présents dans la fenêtre principale ;
- mois manquants dans la fenêtre principale ;
- nombre de séances et mois présents dans 2020–2022 ;
- mois de stress manquants ;
- statut principal et statut stress ;
- `primary_calibration_eligible` et `stress_library_eligible`.

Statuts principaux :

- `PRIMARY_FULL_FROM_ANCHOR` : présence dès l'ancre 2023 (tolérance calendrier 7 jours), fraîcheur <=7 jours et aucun mois entier absent ;
- `PRIMARY_MISSING_CALENDAR_MONTHS` : un ou plusieurs mois entiers manquent ;
- `STALE_END` : historique non suffisamment récent ;
- `START_AFTER_ANCHOR_UNRESOLVED` : l'historique commence après l'ancre, sans preuve fiable que l'instrument a été lancé/coté après 2023 ;
- `NO_PRIMARY_HISTORY`, `NO_CACHE_HISTORY`, `NO_TICKER` : absence explicite.

Statuts stress : `STRESS_FULL_2020_2022`, `STRESS_PARTIAL`, `NO_STRESS_HISTORY`.

## Gates d'éligibilité fail-closed

L'audit de profondeur n'est pas un simple reporting. Il définit les ensembles utilisables par les futures calibrations :

- calibration principale : **uniquement** `PRIMARY_FULL_FROM_ANCHOR` ;
- bibliothèque de stress : **uniquement** `STRESS_FULL_2020_2022` ;
- historique court, mois manquant, cache absent, ticker absent ou série stale : **exclusion** jusqu'à preuve/correction ;
- aucune imputation neutre et aucune date de lancement inventée ;
- le stress reste une bibliothèque de robustesse avec poids de calibration ordinaire égal à **0**.

Ainsi, une série courte n'est jamais transformée silencieusement en série complète et une absence historique ne reçoit jamais une valeur synthétique.

## Instruments lancés après 2023

V21.13 ne déduit jamais une date de lancement à partir de la seule première observation Yahoo. Un historique commençant en 2024/2025 peut représenter soit une introduction réelle, soit une lacune fournisseur.

Les cas concernés restent `START_AFTER_ANCHOR_UNRESOLVED` et sont exclus de la calibration principale jusqu'à attribution d'une date de cotation/inception issue d'une source suffisamment fiable (Euronext/issuer ou autre source gouvernée). Une future preuve de lancement postérieur à 2023 pourra conduire à une politique spécifique, mais ne sera jamais déduite de la seule première barre Yahoo.

## Run réel de qualification du 20/08/2026

Le workflow temporaire de preuve a restauré le cache antérieur `ohlcv-v3-32293890071`, puis a exécuté la migration sur l'univers complet. Les anciens manifests dépourvus de `bootstrap_start` ont été invalidés et les deux univers ont été reconstruits en `FULL_BOOTSTRAP` avec `bootstrap_start=2020-01-01`.

Résultat de collecte :

- Actions avec ticker demandé : 1 790 ; succès 1 783 ; échecs Yahoo 7 ;
- ETF : 102 demandés ; succès 101 ; échec Yahoo 1 ;
- 39 Actions restent sans ticker gouverné et ne sont donc pas requêtées ;
- aucun échec de lecture des caches reconstruits.

Profondeur observée sur les 1 931 instruments canoniques :

- `PRIMARY_FULL_FROM_ANCHOR` : **1 687** ;
- `START_AFTER_ANCHOR_UNRESOLVED` : **190** ;
- `PRIMARY_MISSING_CALENDAR_MONTHS` : **7** ;
- `NO_CACHE_HISTORY` : **8** ;
- `NO_TICKER` : **39** ;
- `STRESS_FULL_2020_2022` : **1 449** ;
- `STRESS_PARTIAL` : **244** ;
- `NO_STRESS_HISTORY` : **191** ;
- `NO_CACHE_HISTORY` stress : **8** ;
- `NO_TICKER` stress : **39**.

En conséquence, au 20/08/2026 :

- **1 687 instruments sont éligibles à la calibration principale** ;
- **1 449 instruments sont éligibles à la bibliothèque de stress** ;
- tous les autres restent fail-closed jusqu'à correction ou preuve complémentaire.

Preuve du run : GitHub Actions run `32379163874`, artefact `V21_13_REAL_OHLCV_QUALIFICATION_32379163874`, SHA-256 de l'archive publiée `1121b0f96f3caaee0c645ea0cf9f89b3663e5fddd98f83dded0a76f87d6795e2`.

## Sorties

- `outputs/audit/OHLCV_PRIMARY_HISTORY_DEPTH.csv`
- `outputs/audit/OHLCV_PRIMARY_HISTORY_DEPTH_SUMMARY.json`

Le résumé publie notamment la borne de bootstrap configurée, le fallback technique, les comptes par statut, les nombres éligibles/exclus et les politiques d'éligibilité.

L'audit est appelé par `criteria_governance_audit`, donc par le Committee hebdomadaire après collecte/restauration du cache.

## Conditions de clôture

La migration V21.13 est clôturable lorsque :

1. CI complet du head final vert ;
2. reconstruction réelle depuis le 01/01/2020 démontrée ;
3. manifests Actions/ETF démontrant `bootstrap_start=2020-01-01` ;
4. rapport réel de profondeur inspecté ;
5. historiques incomplets classés fail-closed via les gates d'éligibilité ;
6. stress exclu de l'optimisation ordinaire ;
7. workflow temporaire de preuve supprimé avant fusion.
