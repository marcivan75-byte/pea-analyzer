# PEA Analyzer — Process de référence final V21.8.1

Date de référence : 18/08/2026

## 1. Statut de gouvernance

Ce document reste la référence opérationnelle unique de production après clôture P0→P5 et audit final. Les challengers ajoutés ensuite y sont enregistrés sans changer la baseline de production tant qu'ils ne satisfont pas leur propre protocole PIT/OOS.

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
- TCT V24.2.0 Intraday/Scalping et V24.2.1 Analytics sont **SHADOW_RESEARCH_ONLY** : influence décision, score, sizing, stop et CT = **0**.
- Le signal T1/T2 journalier connu à la clôture de J ne peut alimenter l'analyse intraday qu'à partir d'une session ultérieure ; V24.2.0 impose actuellement **J+1 au plus tôt**.
- CT reste gelé pendant le chantier TCT V24.2.x ; aucun transfert de variable intraday/scalping vers CT n'est autorisé sans démonstration spécifique ultérieure.
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

### 2.4 Challenger TCT V24.2.x — Intraday / Scalping

Le chantier TCT actif utilise le scalping comme **couche de micro-timing et d'observation**, jamais comme stratégie autonome et jamais comme autorité de décision de production.

Chaîne de recherche :

`PEA ACTION → baseline TCT → T1/T2 exact → ledger PIT → J+1..J+3 intraday 5m → diagnostic SHADOW → analytics SHADOW`.

V24.2.0 observe notamment :

- VWAP cumulatif de séance, distance et pente ;
- RVOL par tranche intraday comparé uniquement aux séances antérieures ;
- accélération du volume ;
- opening range, actionnable seulement après sa clôture ;
- breakout, breakout/retest et VWAP reclaim ;
- volatilité/range intraday ;
- turnover relatif et momentum 5 minutes ;
- spread et order-flow imbalance uniquement lorsqu'ils sont réellement disponibles.

Quatre setups de recherche sont séparés : `EXPLOSIVE_BREAKOUT`, `BREAKOUT_RETEST`, `VWAP_RECLAIM`, `OPENING_RANGE_BREAKOUT`.

Le score V24.2.0 est diagnostique uniquement. Les poids et seuils initiaux sont pré-enregistrés dans `config/TCT_V24_2_0_INTRADAY_SHADOW.json` et ne peuvent pas être retunés sur les premiers résultats.

V24.2.1 mesure automatiquement l'espérance brute à la clôture, taux positif, gain/perte moyens, profit factor brut, MFE/MAE et segmentations par setup, T1/T2, rang J+1/J+2/J+3, tranche horaire et tranche de score. L'espérance nette n'est pas calculée tant que la couverture de friction réelle n'est pas suffisante.

Seuils de maturité pré-enregistrés :

- <10 entrées SHADOW : `ACCUMULATING_EARLY` ;
- 10–29 : `ACCUMULATING_DESCRIPTIVE_ONLY` ;
- ≥30 : revue candidate possible uniquement après contrôle de diversité ;
- ≥10 ISIN distincts pour une revue candidate ;
- ≥15 entrées par setup pour considérer son sous-échantillon suffisamment alimenté.

Même après ces seuils, le statut maximal automatique reste `READY_FOR_PRE_REGISTERED_REVIEW_NOT_PROMOTION`. Une hypothèse candidate doit ensuite être gelée et validée séparément en PIT/OOS avant toute autorité de production.

## 3. Restitution Comité d’Investissement

### 3.1 Android

Le Control Center Android est généré à partir des mêmes décisions canoniques que la restitution PC. Il expose au minimum :

- Actions/ETF sélectionnés et classement ;
- horizon, décision, score et couverture ;
- principaux facteurs positifs et négatifs ;
- contexte V21.8 Entry/Exit ;
- warnings Risk/Bêta et Sector/Theme lorsqu’ils existent ;
- mention explicite qu’aucun ordre réel n’est créé.

Le workflow TCT/CT quotidien produit une synthèse tactique compacte. Les sorties V24.2.0/V24.2.1 sont publiées dans des blocs Android **SHADOW séparés** et ne peuvent pas modifier la synthèse canonique. Le workflow hebdomadaire produit le Control Center Comité complet.

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

Android et PC canoniques proviennent du **même run canonique** et des mêmes décisions. Aucune divergence de score, version, statut ou gouvernance n’est autorisée. Les rapports SHADOW V24.2.x doivent être identifiés comme tels et ne sont pas assimilés à des décisions du Comité.

## 4. Architecture GitHub optimisée

### 4.1 COLLECT + TCT/CT quotidien

Workflow : `committee_tct_ct_daily.yml`.

Cadence : jours ouvrés.

Rôle :

1. restaurer cache OHLCV, provenance et états ;
2. effectuer la collecte/enrichissement quotidien ;
3. recalculer uniquement les horizons TCT/CT et leurs dépendances ;
4. produire la synthèse Android tactique canonique ;
5. exécuter ensuite V24.2.0 Intraday/Scalping SHADOW, de façon non bloquante ;
6. exécuter V24.2.1 Analytics SHADOW, de façon non bloquante ;
7. sauvegarder les caches, états et ledgers PIT ;
8. uploader un artefact compact à rétention courte.

Une panne de la couche V24.2.x ne doit jamais bloquer ou altérer le run TCT/CT canonique.

### 4.2 WEEKLY_HEAVY

Workflow : `committee_master_daily.yml`, désormais workflow lourd hebdomadaire.

Cadence : vendredi.

Rôle : MT/LT, ETF MT, Gold, IPO, Sector/Theme, Comité complet, Risk/Bêta, explicabilité Android/PC et audit de gouvernance des critères.

### 4.3 ETF MT diagnostic

`etf_mt_v20_8_daily.yml` n’est plus planifié quotidiennement. Il reste disponible à la demande pour validation/diagnostic. Le calcul ETF MT de production appartient au run hebdomadaire lourd.

### 4.4 Cache et artefacts

- cache pip activé ;
- cache OHLCV journalier persistant ;
- cache intraday 5 minutes séparé pour le challenger TCT ;
- provenance et états V21.8/TCT réutilisés ;
- ledger PIT des signaux V24.2.0 : `state/TCT_V24_2_0_SIGNAL_LEDGER.csv` ;
- ledger PIT des observations : `state/TCT_V24_2_0_INTRADAY_OBSERVATIONS.csv` ;
- concurrence avec `cancel-in-progress` pour éviter les runs obsolètes ;
- artefact quotidien compact, rétention 7 jours ;
- artefact hebdomadaire complet, rétention 14 jours.

## 5. Benchmark coût/performance disponible

Le benchmark statique validé par les tests de workflow établit :

- full Committee lourd planifié : **5 fois/semaine → 1 fois/semaine**, soit -80 % de déclenchements lourds planifiés ;
- ETF MT standalone planifié : **5 fois/semaine → 0**, désormais on-demand, le calcul utile restant intégré au weekly heavy ;
- TCT/CT : conservation d’une cadence quotidienne adaptée au timing ;
- V24.2.x : collecte limitée aux candidats TCT concernés et exécution SHADOW non bloquante afin d'éviter un recalcul intraday de tout l'univers ;
- suppression du full Committee automatique sur chaque push du workflow lourd ; la validation code reste assurée par les CI dédiées ;
- réutilisation des caches et réduction de la rétention des artefacts.

Ce benchmark ne prétend pas convertir ces réductions en euros tant qu’un historique suffisant de la nouvelle architecture n’existe pas. La mesure de référence à maintenir après mise en production est : minutes runner/jour et semaine, appels API, cache hit, taille artefacts, fraîcheur, couverture et stabilité des décisions.

## 6. Validation de non-dégradation

Audit final de la baseline V21.8.1 :

- Full audit GitHub Actions #32119662770 : **SUCCESS** ; compilation, Ruff, audit statique, intégrité référentielle/gouvernance et suite pytest complète verts.
- ETF MT validation #32119662757 : **SUCCESS** ; configuration, compilation et tests de régression/workflow ETF MT verts.
- Aucune modification de poids/seuil pendant la correction finale.
- Aucun déverrouillage du holdout.
- Aucun ordre réel.

Validation du challenger TCT V24.2.x :

- PR #79 — V24.2.0 Intraday/Scalping SHADOW : full audit #32187851396 **SUCCESS** ; ETF MT non-régression #32187851393 **SUCCESS** ; tests dédiés de causalité, isolation du canonique et intégration J→J+1 verts ; merge `f5c1616`.
- PR #80 — V24.2.1 Analytics SHADOW : full audit #32188361478 **SUCCESS** ; compilation, Ruff, audit statique, intégrité et suite pytest complète verts ; merge `5515ac8`.
- Aucun changement des pondérations ou seuils de production.
- Aucun déverrouillage du holdout.
- Aucun ordre réel.
- Les résultats statistiques du challenger restent **NON VALIDÉS** tant que l'échantillon PIT réel n'a pas atteint les seuils de maturité pré-enregistrés et subi une validation séparée.

Les tests dédiés couvrent notamment : câblage Android/PC, gouvernance des critères, cadence quotidienne/hebdomadaire, caches persistants, optimisation batch GDELT/consensus/OHLCV, daily TCT/CT, non-régression ETF MT, causalité intraday, interdiction d'exécution same-day après un signal journalier, isolation SHADOW et maturité analytique sans retuning.

## 7. Runbook opérationnel

### Chaque jour ouvré

1. `PEA Daily Collect + TCT CT V21.8.1`.
2. Vérifier audit de collecte, cache/provenance, qualité des données et synthèse Android TCT/CT canonique.
3. Vérifier séparément `TCT_INTRADAY_V24_2_0_AUDIT.json` et `TCT_INTRADAY_V24_2_1_ANALYTICS.json` lorsque présents ; les traiter comme recherche SHADOW uniquement.
4. En cas d’échec V24.2.x : conserver le run canonique s'il est vert et corriger la cause SHADOW démontrée ; ne pas propager l'échec au Comité.
5. En cas d’échec canonique : corriger la cause démontrée ; ne pas déclencher les modules lourds par défaut.

### Chaque vendredi

1. `PEA Weekly Heavy Committee V21.8.1`.
2. Vérifier quality gates, Comité, ETF MT, Gold, IPO, Sector/Theme, Risk, Entry/Exit V21.8, audit des critères.
3. Publier Android Comité complet et restitution PC explicative.

### Sur événement matériel

Recalcul ciblé uniquement si la donnée ou l’événement justifie de ne pas attendre la cadence normale. Éviter le full run si un module ciblé suffit.

### Release / audit

Un full run ou PIT/OOS n’est déclenché que pour un changement runtime matériel, une release, une non-régression planifiée ou une question statistique qui ne peut pas être validée autrement.

Pour V24.2.x, aucune optimisation n'est autorisée pendant la phase `ACCUMULATING_EARLY` ou `ACCUMULATING_DESCRIPTIVE_ONLY`. Lorsque la maturité minimale est atteinte, une hypothèse candidate doit être pré-enregistrée et gelée avant la prochaine validation.

## 8. Règle WIP=1 après clôture

Le processus reste WIP=1 : une seule amélioration active à la fois. Toute nouvelle idée est mise en file d'attente jusqu'à clôture auditée du chantier courant.

**Chantier actif au 18/08/2026 : TCT V24.2.x Intraday/Scalping.** Le sous-bloc technique V24.2.0/V24.2.1 est intégré et validé en non-régression, mais le chantier statistique reste ouvert pendant l'accumulation PIT. Le CT reste gelé pendant cette phase.

La prochaine optimisation des pondérations, le transfert au CT ou toute promotion TCT ne doit démarrer qu’après accumulation d’un historique PIT suffisant et démonstration OOS d’un gain robuste. La production V21.8.1 reste la référence tant qu’une nouvelle version n’a pas satisfait cette même Definition of Done.
