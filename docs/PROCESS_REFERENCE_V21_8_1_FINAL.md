# PEA Analyzer — Process de référence final V21.8.1

Date de référence : 18/08/2026

## 1. Statut de gouvernance

Ce document est la référence opérationnelle unique après clôture P0→P5 et audit final.

- Baseline Entry/Exit : **V21.8.1**.
- Holdout final : **FERMÉ**.
- T1/T2 : **ACTION TCT uniquement** ; T2 exact requis lorsque la règle TCT l’exige.
- Aucun take-profit fixe opérationnel.
- Ancien objectif +4 % : non opérationnel.
- Ancien stop ETF -18 % : non opérationnel.
- Aucun nouveau hard stop promu ; -7 % reste un plafond de recherche, pas une règle de production.
- Performance virtuelle legacy : **SKIPPED_GOVERNANCE** dans le runtime V21.8.1.
- Aucun ordre réel ; le système reste une aide à la décision.
- Sector/Theme Rotation V2 et Risk V1.1/Bêta restent **CONTEXT_ONLY** tant qu’une promotion PIT/OOS n’est pas explicitement validée.
- Aucune nouvelle pondération, règle ou seuil ne peut être promu sans preuve PIT/OOS/backtest robuste.

## 2. Critères, pondérations et explicabilité

### 2.1 Références actives

Les décisions Actions de production restent rattachées à la référence V21.0 gelée. Les critères ajoutés uniquement dans le challenger restent SHADOW et ont une influence décisionnelle nulle tant qu’ils ne sont pas promus selon la gouvernance.

Le cœur ETF MT V20.8.1 conserve ses 38 critères dynamiques et son attribution historique de 90,91 % uniquement dans le sous-bloc pour lequel cette attribution a été démontrée. Aucune extension implicite à l’ensemble des critères ETF n’est autorisée.

### 2.2 Audit de statut des critères

`criteria_governance_audit` publie une vue exécutable distinguant :

- `ACTIVE` : critère de la référence de production avec donnée disponible ;
- `SHADOW` : critère challenger sans autorité de décision ;
- `CONTEXT_ONLY` : information diagnostique sans influence score/décision/sizing/stop ;
- `MISSING` : critère gouverné sans donnée exploitable ;
- `BLOCKED` : promotion ou autorité explicitement interdite.

Les poids de code/config/référentiels doivent rester cohérents. Aucun changement de poids n’a été réalisé pendant l’audit final, la couverture Actions étant insuffisante pour justifier une nouvelle optimisation.

### 2.3 Couverture à améliorer avant repondération

Mesure issue de l’artefact réel `main` #32064095330 :

- Actions CT : ~52,98 % de couverture pondérée ;
- Actions MT : ~44,71 % ;
- Actions LT : ~48,49 % ;
- Actions SHORT : ~58,32 % ;
- Actions TOP_DOWN : ~45,36 %.

Priorités de données : consensus/targets, révisions brokers, earnings catalysts, news catalysts, Morningstar Action et autres champs à faible couverture identifiés dans l’audit. La priorité est **l’enrichissement et la provenance**, pas la repondération prématurée.

## 3. Restitution Comité d’Investissement

### 3.1 Android

Le Control Center Android est généré à partir des mêmes décisions canoniques que la restitution PC. Il expose au minimum :

- Actions/ETF sélectionnés et classement ;
- horizon, décision, score et couverture ;
- principaux facteurs positifs et négatifs ;
- contexte V21.8 Entry/Exit ;
- warnings Risk/Bêta et Sector/Theme lorsqu’ils existent ;
- mention explicite qu’aucun ordre réel n’est créé.

Le workflow TCT/CT quotidien produit une synthèse tactique compacte. Le workflow hebdomadaire produit le Control Center Comité complet.

### 3.2 PC détaillée

La restitution PC publie, pour chaque titre Action/ETF sélectionné, une vue par critère comportant :

- identité du titre, classe d’actif et horizon ;
- critère et statut de gouvernance ;
- valeur brute ;
- champ/source/provenance disponible ;
- score normalisé 0–100 ;
- poids théorique ;
- poids effectif après gestion des données manquantes ;
- contribution pondérée ;
- score final et décision canonique.

La reconstruction des contributions est contrôlée contre le score publié lorsque le moteur permet une reconstruction exacte. Une donnée non persistée ne doit jamais être inventée.

### 3.3 Règle de cohérence

Android et PC proviennent du **même run canonique** et des mêmes décisions. Aucune divergence de score, version, statut ou gouvernance n’est autorisée.

## 4. Architecture GitHub optimisée

### 4.1 COLLECT + TCT/CT quotidien

Workflow : `committee_tct_ct_daily.yml`.

Cadence : jours ouvrés.

Rôle :

1. restaurer cache OHLCV, provenance et états ;
2. effectuer la collecte/enrichissement quotidien ;
3. recalculer uniquement les horizons TCT/CT et leurs dépendances ;
4. produire la synthèse Android tactique ;
5. sauvegarder les caches/états ;
6. uploader un artefact compact à rétention courte.

### 4.2 WEEKLY_HEAVY

Workflow : `committee_master_daily.yml`, désormais workflow lourd hebdomadaire.

Cadence : vendredi.

Rôle : MT/LT, ETF MT, Gold, IPO, Sector/Theme, Comité complet, Risk/Bêta, explicabilité Android/PC et audit de gouvernance des critères.

### 4.3 ETF MT diagnostic

`etf_mt_v20_8_daily.yml` n’est plus planifié quotidiennement. Il reste disponible à la demande pour validation/diagnostic. Le calcul ETF MT de production appartient au run hebdomadaire lourd.

### 4.4 Cache et artefacts

- cache pip activé ;
- cache OHLCV persistant ;
- provenance et états V21.8/TCT réutilisés ;
- concurrence avec `cancel-in-progress` pour éviter les runs obsolètes ;
- artefact quotidien compact, rétention 7 jours ;
- artefact hebdomadaire complet, rétention 14 jours.

## 5. Benchmark coût/performance disponible

Le benchmark statique validé par les tests de workflow établit :

- full Committee lourd planifié : **5 fois/semaine → 1 fois/semaine**, soit -80 % de déclenchements lourds planifiés ;
- ETF MT standalone planifié : **5 fois/semaine → 0**, désormais on-demand, le calcul utile restant intégré au weekly heavy ;
- TCT/CT : conservation d’une cadence quotidienne adaptée au timing ;
- suppression du full Committee automatique sur chaque push du workflow lourd ; la validation code reste assurée par les CI dédiées ;
- réutilisation des caches et réduction de la rétention des artefacts.

Ce benchmark ne prétend pas convertir ces réductions en euros tant qu’un historique suffisant de la nouvelle architecture n’existe pas. La mesure de référence à maintenir après mise en production est : minutes runner/jour et semaine, appels API, cache hit, taille artefacts, fraîcheur, couverture et stabilité des décisions.

## 6. Validation de non-dégradation

Audit final du head de la PR #78 :

- Full audit GitHub Actions #32119662770 : **SUCCESS** ; compilation, Ruff, audit statique, intégrité référentielle/gouvernance et suite pytest complète verts.
- ETF MT validation #32119662757 : **SUCCESS** ; configuration, compilation et tests de régression/workflow ETF MT verts.
- Aucune modification de poids/seuil pendant la correction finale.
- Aucun déverrouillage du holdout.
- Aucun ordre réel.

Les tests dédiés couvrent notamment : câblage Android/PC, gouvernance des critères, cadence quotidienne/hebdomadaire, caches persistants, optimisation batch GDELT/consensus/OHLCV, daily TCT/CT et non-régression ETF MT.

## 7. Runbook opérationnel

### Chaque jour ouvré

1. `PEA Daily Collect + TCT CT V21.8.1`.
2. Vérifier audit de collecte, cache/provenance, qualité des données et synthèse Android TCT/CT.
3. En cas d’échec : corriger la cause démontrée ; ne pas déclencher les modules lourds par défaut.

### Chaque vendredi

1. `PEA Weekly Heavy Committee V21.8.1`.
2. Vérifier quality gates, Comité, ETF MT, Gold, IPO, Sector/Theme, Risk, Entry/Exit V21.8, audit des critères.
3. Publier Android Comité complet et restitution PC explicative.

### Sur événement matériel

Recalcul ciblé uniquement si la donnée ou l’événement justifie de ne pas attendre la cadence normale. Éviter le full run si un module ciblé suffit.

### Release / audit

Un full run ou PIT/OOS n’est déclenché que pour un changement runtime matériel, une release, une non-régression planifiée ou une question statistique qui ne peut pas être validée autrement.

## 8. Règle WIP=1 après clôture

Le processus reste WIP=1 : une seule amélioration active à la fois. Toute nouvelle idée est mise en file d’attente jusqu’à clôture auditée du chantier courant.

La prochaine optimisation des pondérations ne doit démarrer qu’après accumulation d’un historique PIT suffisant et démonstration OOS d’un gain robuste. Le process ci-dessus reste la référence tant qu’une nouvelle version n’a pas satisfait cette même Definition of Done.
