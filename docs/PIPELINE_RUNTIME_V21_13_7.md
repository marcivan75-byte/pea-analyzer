# Pipeline rationalisé et runtime V21.13.7

## Périmètre actif

V21.13.7 retire entièrement du runtime, des workflows planifiés, des sorties et
des validations actives :

- Actions LT ;
- ETF LT ;
- processus Gold ;
- processus Crypto/ETP ;
- processus IPO.

Les horizons conservés sont Actions TCT/CT/MT et ETF PEA CT/MT, avec les
modules transverses encore nécessaires à ces horizons. Les formules, poids,
seuils et gates des horizons conservés ne sont pas retunés par cette évolution.

La preuve statique de date de cotation déjà acquise pour les Actions reste un
référentiel d'identité figé. Aucun collecteur, radar, scoring ou workflow IPO
n'est encore actif.

## PREOPEN ciblé

Le PREOPEN autonome est conservé à 06:40 UTC du lundi au vendredi. Son seed est
préparé par le run tactique précédent et limité à l'union suivante :

- les 20 Actions présélectionnées TCT ;
- les 20 meilleures Actions CT dont la décision est `BUY_CANDIDATE`, `WATCH`
  ou `REVIEW` ;
- déduplication par ISIN, avec un plafond absolu de 40 titres.

Le même périmètre borné est utilisé par le snapshot POSTMARKET consolidé afin
de ne pas réintroduire une collecte catalyst large dans les jobs principaux.
Si le marqueur de présélection manque, la collecte échoue fermée : elle ne
retombe jamais sur tout l'univers.

## Architecture des jobs

- Le quotidien démarre à 21:00 UTC du lundi au jeudi et inclut son POSTMARKET.
- L'hebdomadaire démarre à 20:40 UTC le vendredi, produit aussi la tactique du
  vendredi et inclut son POSTMARKET.
- PREOPEN reste le seul job catalyst autonome.
- Les compilations et validations lourdes sont couvertes par la CI ; elles ne
  sont exécutées sur un schedule qu'en mode manuel explicite.

## Budget mensuel central

Un mois moyen correspond à 4,348 semaines. GitHub arrondit les minutes
facturables de chaque job privé sur runner hébergé à la minute supérieure ; le
budget applique donc cet arrondi avant la multiplication par la fréquence.
[Documentation GitHub](https://docs.github.com/enterprise-cloud%40latest/actions/how-tos/monitor-workflows/view-job-execution-time).

| Job | Runs/mois | Temps mur cible | Budget facturable/run | Minutes facturables/mois |
|---|---:|---:|---:|---:|
| Quotidien + POSTMARKET | 17,392 | 10,1 min | 11 min | 191,3 |
| Hebdo + tactique vendredi + POSTMARKET | 4,348 | 25,2 min | 26 min | 113,0 |
| PREOPEN ciblé | 21,740 | 1,2 min | 2 min | 43,5 |
| **Total** | **43,480 jobs** | **311,3 min** | — | **347,8 min / 5 h 48** |

La cible d'exploitation est de 350 minutes facturables par mois et le seuil
d'alerte de 380 minutes. Par rapport au budget V21.13.6 de 404,4 minutes, le
gain central estimé est de 56,6 minutes par mois, soit 14,0 %.

Ces valeurs sont des budgets prévisionnels. Les durées réelles et minutes
facturables affichées par GitHub après plusieurs runs restent la référence. Les
minutes des dépôts privés consomment d'abord le quota inclus dans le plan, puis
peuvent être facturées au-delà de ce quota ; les dépôts publics et les runners
self-hosted suivent une autre règle.
[Règles de facturation GitHub Actions](https://docs.github.com/en/actions/concepts/billing-and-usage).

## Télémétrie

Les fichiers courants sont :

- `outputs/audit/PIPELINE_RUNTIME_V21_13_7.json` et `.csv` ;
- `outputs/audit/UNIFIED_RUNTIME_V21_13_7.json` et `.csv` ;
- `outputs/audit/TCT_NEXT_SESSION_CATALYST_V24_4_2_AUDIT.json`.

Le contrôle opérationnel compare les durées observées aux cibles 10,1 / 25,2 /
1,2 minute et les minutes facturables par job aux budgets 11 / 26 / 2.

## Invariants

- Univers canoniques inchangés : 1 829 Actions et 102 ETF PEA.
- T1/T2 restent exclusivement Action TCT.
- ETF Fund Flows PEA reste SHADOW avec influence décisionnelle nulle.
- Aucun ordre réel ; holdout fermé.
- Aucun fallback vers les périmètres supprimés.
