# Pipeline runtime V21.13.4

## Objectif

V21.13.4 réduit le temps de collecte et de traitement sans modifier l'univers,
les critères, les pondérations, les seuils, les décisions ou les règles de
promotion. La cible opérationnelle reste un run tactique quotidien inférieur à
5 minutes ; elle doit être vérifiée sur les prochains runs réels, et non déduite
d'une estimation locale.

## Collecte évitée

Le pipeline hebdomadaire collectait l'OHLCV des 102 ETF dans la vague 02, puis
le module ETF MT lançait immédiatement une seconde passe Yahoo dans son cache
dédié. Après un refresh réussi, ETF MT lit désormais directement
`data/cache/etf/` avec `network_collection_executed=false`. Son exécution
autonome conserve son propre refresh incrémental : le mode de secours et les
runs manuels restent donc fonctionnels.

ETF Fund Flows est un contexte SHADOW à cadence `WEEKLY_OR_NEW_AS_OF`. La
collecte planifiée est portée par le Comité hebdomadaire du vendredi. Le
workflow autonome devient manuel et ses tests ciblés sont activables par
`run_validation`; il ne duplique plus la collecte chaque jour ouvré. On passe
ainsi de six déclenchements planifiés par semaine (cinq autonomes plus le
Comité) à un seul, sans supprimer la collecte hebdomadaire canonique.

## Traitement local réduit

- Les branches Action et ETF de la vague 03 sont calculées en parallèle avec
  deux workers locaux bornés.
- Les parquets ETF sont lus une seule fois puis partagés entre les indicateurs
  dérivés et le bêta 3 ans.
- Le profil `DAILY_TACTICAL` conserve les CSV enrichis, les quality gates et le
  classeur final de disponibilité, mais ne construit plus les trois classeurs
  maîtres non publiés par le workflow quotidien.
- Dans ce profil, les audits intermédiaires restent complets en CSV ; seul
  `WAVE_99_FINAL` construit et formate le classeur Excel `LATEST`. Le profil
  hebdomadaire complet conserve tous les exports historiques.

## Télémétrie

Chaque enrichissement publie :

- `outputs/audit/PIPELINE_RUNTIME_V21_13_4.json` ;
- `outputs/audit/PIPELINE_RUNTIME_V21_13_4.csv`.

Les fichiers exposent le temps mur et CPU par étape, les totaux
`COLLECTION`/`PROCESSING`/`OUTPUT`, le profil et l'étape active. Un checkpoint
est écrit à chaque transition ; une panne conserve donc les étapes terminées et
le nom de l'étape en cours.

Le runner unifié ajoute :

- `outputs/audit/UNIFIED_RUNTIME_V21_13_4.json` ;
- `outputs/audit/UNIFIED_RUNTIME_V21_13_4.csv`.

Les prochains runs réels doivent comparer au minimum le temps total, le chemin
critique, le nombre de refresh réseau, les cache hits, la fraîcheur, la
couverture et la stabilité des décisions.

## Benchmark local indicatif

Sur les masters d'entrée du dépôt (Actions `1486 x 208`, ETF `102 x 76`), le
22 août 2026 :

| Opération locale | Temps mur |
|---|---:|
| Trois exports Excel finaux | 6,45 s |
| Un audit intermédiaire Excel | 0,302 s |
| Le même audit compact CSV | 0,131 s |

Ce benchmark confirme le coût local supprimé, mais ne mesure ni la latence des
providers ni le master enrichi de production. Il ne remplace donc pas la
télémétrie GitHub V21.13.4.

## Invariants de gouvernance

- 1 829 Actions et 102 ETF inchangés.
- T1/T2 restent exclusivement Action TCT.
- ETF Fund Flows reste SHADOW avec influence décisionnelle nulle.
- Aucun poids, seuil, critère, gate, statut de promotion ou ordre n'est changé.
- Holdout fermé et ordres réels désactivés.
- Toute absence du cache ETF primaire déclenche explicitement le refresh
  incrémental de secours ; aucune donnée vide ou inventée n'alimente ETF MT.
