# Pipeline runtime V21.13.5

## Objectif

V21.13.5 fixe un budget explicite aux workflows planifiés sans modifier les
univers, critères, pondérations, seuils, décisions, promotions ou ordres. La
cible est de ramener les deux pipelines principaux sous 300 minutes par mois
moyen et l'ensemble des workflows planifiés sous 390 minutes. Le seuil d'alerte
est 420 minutes, soit 7 heures.

Ces valeurs sont des budgets d'exploitation, pas une garantie. Les temps réels
publiés par GitHub et les audits runtime restent la référence.

## Cadence mutualisée

| Charge | Cadence V21.13.5 | Propriétaire |
|---|---|---|
| Collecte et tactique quotidienne | lundi à jeudi, 18:15 UTC | workflow quotidien |
| Collecte complète, Comité et tactique du vendredi | vendredi, 18:45 UTC | workflow hebdomadaire |
| Catalyst next-session | PREOPEN et POSTMARKET, cinq jours ouvrés | workflow catalyst |

Le vendredi ne lance donc plus deux environnements GitHub. Après le runner
unifié, le workflow hebdomadaire exécute les mêmes modules tactiques que le
quotidien : ETF structure, ETF CT/LT, TCT/CT V21.8, Action CT V22.0 et V22.1,
TCT V24.3.1 et ledger OHLC V24.4.2. Les états correspondants sont restaurés et
sauvegardés dans les mêmes namespaces de cache.

## Collecte lente cache-prioritaire

Sur les runs du lundi au jeudi, `PEA_SLOW_SOURCE_MODE=CACHE_PREFERRED` s'applique
à Yahoo Fundamentals, Finnhub Consensus et Yahoo ETF Info.

- Une entrée valide est hydratée depuis le cache avec son timestamp source
  original.
- Un refresh simplement dû au TTL est différé au vendredi.
- Un instrument absent ou devenu plus vieux que la limite dure déclenche
  toujours une collecte réseau de secours, même en mode cache-prioritaire.
- Le vendredi reste en mode `LIVE` et applique les priorités HOT/WARM/COLD et
  les budgets de refresh existants.
- OHLCV, top-down et news conservent leur cadence quotidienne car TCT/CT dépend
  de l'état de marché courant.

Le mode n'invente aucune valeur, ne redâte aucun cache et ne réduit pas
l'univers canonique.

Le calcul local Action CT V22.0 utilise désormais quatre workers à partir de
50 historiques exploitables. `executor.map` conserve l'ordre du master et le
wrapper d'erreur reste fail-closed ; les formules et les sorties ligne à ligne
sont inchangées.

## Validation CI

Les compilations, audits statiques et contrôles référentiels sont déjà exécutés
sur les pull requests. Ils ne sont plus répétés sur chaque déclenchement
planifié. Le paramètre manuel `run_validation=true` les réactive pour un run de
diagnostic du quotidien, de l'hebdomadaire ou du catalyst.

Le snapshot catalyst PREOPEN conserve sa collecte et son scoring. Il ne relance
plus le lineage OHLC et le validateur PIT, puisqu'aucun nouvel OHLC quotidien
n'est apparu depuis le POSTMARKET précédent. Ce post-traitement reste quotidien
au POSTMARKET et reste exécutable sur tout lancement manuel.

## Budget mensuel

Un mois moyen correspond à 4,348 semaines.

| Workflow principal | Runs/mois | Cible par run | Budget central |
|---|---:|---:|---:|
| Quotidien lundi-jeudi | 17,392 | 9 min | 156,5 min |
| Hebdo vendredi avec tactique | 4,348 | 32 min | 139,1 min |
| Total principal | 21,740 | — | 295,7 min / 4 h 56 |
| Catalyst PREOPEN/POSTMARKET | 43,480 | 2,1 min | 91,3 min |
| Total planifié central | 65,220 | — | 387,0 min / 6 h 27 |

La plage de dimensionnement est de 7 à 11 minutes pour un quotidien à cache
chaud et de 28 à 38 minutes pour l'hebdo avec sa fin tactique. Le workflow
catalyst conserve 43,48 snapshots par mois moyen ; sa cible est 2,1 minutes par
snapshot et il dispose au maximum de 94,3 minutes dans la cible globale de
390 minutes. Si sa moyenne dépasse
2 min 10 s par snapshot, la cible globale est dépassée ; au-delà de 2 min 51 s,
le seuil d'alerte de 7 heures est dépassé.

## Télémétrie

L'enrichissement publie :

- `outputs/audit/PIPELINE_RUNTIME_V21_13_5.json` ;
- `outputs/audit/PIPELINE_RUNTIME_V21_13_5.csv`.

Le runner unifié publie :

- `outputs/audit/UNIFIED_RUNTIME_V21_13_5.json` ;
- `outputs/audit/UNIFIED_RUNTIME_V21_13_5.csv`.

Les audits Yahoo et Finnhub exposent aussi `refresh_due_enabled` et
`due_refresh_suppressed`. Les premiers runs réels doivent confirmer les temps
par étape, le nombre de refreshs réseau, les cache hits, la fraîcheur, la
couverture et la stabilité des décisions.

## Invariants de gouvernance

- 1 829 Actions et 102 ETF inchangés.
- T1/T2 restent exclusivement Action TCT.
- ETF Fund Flows reste SHADOW avec influence décisionnelle nulle.
- Aucun poids, seuil, critère, gate, statut de promotion ou ordre n'est changé.
- Holdout fermé et ordres réels désactivés.
- Tout cache manquant ou hors limite déclenche une récupération réseau ; une
  donnée vide ou inventée ne peut jamais alimenter le pipeline.
