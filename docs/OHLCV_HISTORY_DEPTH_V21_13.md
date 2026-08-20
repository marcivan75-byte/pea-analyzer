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
- statut principal et statut stress.

Statuts principaux :

- `PRIMARY_FULL_FROM_ANCHOR` : présence dès l'ancre 2023 (tolérance calendrier 7 jours), fraîcheur <=7 jours et aucun mois entier absent ;
- `PRIMARY_MISSING_CALENDAR_MONTHS` : un ou plusieurs mois entiers manquent ;
- `STALE_END` : historique non suffisamment récent ;
- `START_AFTER_ANCHOR_UNRESOLVED` : l'historique commence après l'ancre, sans preuve fiable que l'instrument a été lancé/coté après 2023 ;
- `NO_PRIMARY_HISTORY`, `NO_CACHE_HISTORY`, `NO_TICKER` : absence explicite.

Statuts stress : `STRESS_FULL_2020_2022`, `STRESS_PARTIAL`, `NO_STRESS_HISTORY`.

## Instruments lancés après 2023

V21.13 ne déduit jamais une date de lancement à partir de la seule première observation Yahoo. Un historique commençant en 2024/2025 peut représenter soit une introduction réelle, soit une lacune fournisseur.

Les cas concernés restent `START_AFTER_ANCHOR_UNRESOLVED` jusqu'à attribution d'une date de cotation/inception issue d'une source suffisamment fiable (Euronext/issuer ou autre source gouvernée). Une introduction réellement postérieure à 2023 ne doit évidemment pas être pénalisée pour l'absence de données antérieures à son existence.

## Sorties

- `outputs/audit/OHLCV_PRIMARY_HISTORY_DEPTH.csv`
- `outputs/audit/OHLCV_PRIMARY_HISTORY_DEPTH_SUMMARY.json`

L'audit est appelé par `criteria_governance_audit`, donc par le Committee hebdomadaire après collecte/restauration du cache.

## Conditions de clôture

La migration V21.13 n'est pas considérée validée sur les seuls tests synthétiques. Il faut :

1. CI complet vert ;
2. run représentatif avec restauration de l'ancien cache puis reconstruction explicite depuis le 01/01/2020 ;
3. inspection des deux manifests Actions/ETF et présence de `bootstrap_start=2020-01-01` ;
4. inspection du rapport réel de profondeur ;
5. résolution ou classement explicite des historiques courts avant toute nouvelle calibration ;
6. aucune utilisation du stress set dans l'optimisation ordinaire.
