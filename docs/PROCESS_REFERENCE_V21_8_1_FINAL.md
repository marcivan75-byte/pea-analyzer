# PEA Analyzer — Process de référence final V21.8.1

Date de référence : 19/08/2026

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
- Challenger actif TCT : **V24.3.0 Daily/Weekly Trader Tools SHADOW**.
- V24.3.0 utilise uniquement les OHLCV quotidiennes déjà collectées ; le weekly est dérivé du daily.
- Données intraday, 5 minutes, quasi temps réel, carnet/order flow live et spread live : **EXCLUS du chantier TCT**.
- V24.3.0 : influence décision, score, sizing, stop et CT = **0**.
- CT reste gelé pendant le chantier TCT V24.3.x ; aucun transfert vers CT sans démonstration spécifique ultérieure.
- V24.2.x Intraday/Scalping est **ABANDONNÉE et retirée du runtime actif** car elle ne correspond pas au besoin fonctionnel final. Elle reste uniquement dans l'historique Git pour traçabilité.
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

Les poids de code/config/référentiels doivent rester cohérents. La couverture Actions restant insuffisante pour justifier une nouvelle optimisation globale, la priorité demeure l'enrichissement et la provenance plutôt qu'une repondération prématurée.

### 2.3 Couverture Actions de référence

Mesure issue de l’artefact réel `main` #32064095330 :

- Actions CT : ~52,98 % de couverture pondérée ;
- Actions MT : ~44,71 % ;
- Actions LT : ~48,49 % ;
- Actions SHORT : ~58,32 % ;
- Actions TOP_DOWN : ~45,36 %.

Priorités de données hors chantier TCT : consensus/targets, révisions brokers, earnings catalysts, news catalysts, Morningstar Action et autres champs à faible couverture identifiés dans l’audit.

### 2.4 Challenger TCT V24.3.0 — outils de traders adaptés au daily/weekly

L'objectif n'est **pas de faire du day trading**. Le système emprunte uniquement des concepts utiles aux traders court terme pour améliorer la décision d'entrée et de sortie sur un horizon TCT de quelques séances, éventuellement proche d'une semaine.

Chaîne de recherche :

`PEA ACTION → baseline TCT → T1/T2 exact → diagnostic Daily/Weekly Trader Tools SHADOW`.

Aucun téléchargement de marché supplémentaire n'est requis : V24.3.0 réutilise `data/cache/actions`.

#### Bloc structure et niveaux

- breakout 20 jours ;
- breakout 55 jours ;
- retest d'un breakout récent ;
- plus hauts/bas récents ;
- pivot/R1/S1 de la séance précédente ;
- plus haut/bas/pivot de la semaine précédente ;
- référence d'invalidation structurelle.

#### Bloc volume et liquidité

- RVOL quotidien vs médiane 20 jours ;
- accélération volume récent vs 20 jours ;
- turnover quotidien médian 20 jours ;
- warning de liquidité lorsque le titre est trop peu négocié pour un TCT propre.

#### Bloc volatilité

- ATR 14 jours ;
- expansion du range quotidien ;
- compression récente ;
- expansion après compression ;
- détection de gap ou extension excessive en unités d'ATR.

#### Bloc price action / qualité de clôture

- position de la clôture dans le range ;
- corps de bougie ;
- mèches haute/basse ;
- direction ouverture/clôture ;
- gap quotidien.

#### Bloc momentum / tendance

- EMA 9 ;
- EMA 20 ;
- pente EMA 9 ;
- rendement 5 jours ;
- rendement 20 jours.

#### Bloc prix pondéré par le volume

V24.3.0 calcule un **prix roulant pondéré par le volume sur 20 et 60 séances** à partir des barres quotidiennes. Ce n'est pas un VWAP intraday de séance. Il sert uniquement à apprécier la position du cours par rapport au prix moyen associé au volume récent et à détecter une sur-extension.

#### Bloc weekly

Le weekly est dérivé des barres daily déjà disponibles :

- moyenne de tendance 10 semaines ;
- momentum 4 semaines ;
- position de clôture dans le range hebdomadaire ;
- niveaux de la semaine précédente.

### 2.5 Scores SHADOW V24.3.0

Score d'entrée pré-enregistré :

- structure breakout/retest : 20 % ;
- volume/liquidité : 15 % ;
- qualité de clôture : 15 % ;
- volatilité : 15 % ;
- momentum/tendance : 15 % ;
- alignement weekly : 15 % ;
- position vs prix pondéré volume : 5 %.

États d'entrée SHADOW :

- `ENTRY_STRONG_SHADOW` ;
- `ENTRY_READY_SHADOW` ;
- `WAIT_PULLBACK_SHADOW` ;
- `LIQUIDITY_WARNING_SHADOW` ;
- `WAIT_SHADOW` ;
- `DATA_INSUFFICIENT`.

Score de risque de sortie pré-enregistré :

- failed breakout / structure : 25 % ;
- passage sous tendance courte : 20 % ;
- distribution avec volume : 15 % ;
- détérioration momentum : 15 % ;
- détérioration weekly : 15 % ;
- volatilité adverse : 10 %.

États de sortie SHADOW :

- `HOLD_SUPPORTIVE_SHADOW` ;
- `EXIT_WATCH_SHADOW` ;
- `EXIT_RISK_HIGH_SHADOW` ;
- `DATA_INSUFFICIENT`.

Ces poids et seuils sont des hypothèses de recherche gelées. Aucun retuning automatique n'est autorisé et aucune performance n'est attribuée à V24.3.0 avant validation.

## 3. Restitution Comité d’Investissement

### 3.1 Android

Le Control Center Android canonique est généré à partir des mêmes décisions que la restitution PC. Il expose notamment :

- Actions/ETF sélectionnés et classement ;
- horizon, décision, score et couverture ;
- principaux facteurs positifs et négatifs ;
- contexte V21.8 Entry/Exit ;
- warnings Risk/Bêta et Sector/Theme lorsqu’ils existent ;
- mention explicite qu’aucun ordre réel n’est créé.

Le workflow TCT/CT quotidien produit une synthèse tactique canonique puis un bloc séparé `ANDROID_TCT_DAILY_TRADER_SHADOW.md`. Ce bloc SHADOW ne peut jamais modifier les décisions canoniques.

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

Une donnée non persistée ne doit jamais être inventée.

### 3.3 Règle de cohérence

Android et PC canoniques proviennent du même run canonique et des mêmes décisions. Les rapports V24.3.0 restent explicitement SHADOW et ne sont pas assimilés à une décision du Comité.

## 4. Architecture GitHub optimisée

### 4.1 COLLECT + TCT/CT quotidien

Workflow : `committee_tct_ct_daily.yml`.

Cadence : jours ouvrés, post-clôture.

Rôle :

1. restaurer le cache OHLCV quotidien, provenance et état T1/T2 ;
2. supprimer tout ancien `data/cache/actions_intraday_5m` éventuellement restauré depuis un cache historique ;
3. effectuer la collecte/enrichissement quotidien déjà prévue ;
4. recalculer TCT/CT et leurs dépendances canoniques ;
5. exécuter V24.3.0 Daily/Weekly Trader Tools SHADOW sans téléchargement de marché supplémentaire ;
6. publier la synthèse Android canonique et le bloc V24.3.0 SHADOW séparé ;
7. sauvegarder uniquement les caches/états utiles ;
8. uploader un artefact compact à rétention courte.

Une panne V24.3.0 ne doit jamais bloquer ou altérer le run TCT/CT canonique.

### 4.2 WEEKLY_HEAVY

Workflow : `committee_master_daily.yml`, workflow lourd hebdomadaire.

Cadence : vendredi.

Rôle : MT/LT, ETF MT, Gold, IPO, Sector/Theme, Comité complet, Risk/Bêta, explicabilité Android/PC et audit de gouvernance des critères.

### 4.3 ETF MT diagnostic

`etf_mt_v20_8_daily.yml` reste disponible à la demande pour validation/diagnostic. Le calcul ETF MT utile au Comité appartient au run hebdomadaire lourd.

### 4.4 Cache et artefacts

- cache pip activé ;
- cache OHLCV journalier persistant ;
- **aucun cache intraday TCT actif** ;
- provenance et états V21.8/TCT réutilisés ;
- concurrence avec `cancel-in-progress` ;
- artefact quotidien compact, rétention 7 jours ;
- artefact hebdomadaire complet, rétention 14 jours.

## 5. Coût / performance

L'architecture conserve la cadence quotidienne adaptée au TCT tout en évitant les coûts inutiles :

- aucun téléchargement 1m/5m ;
- aucun flux quasi temps réel ;
- aucun fournisseur supplémentaire requis par V24.3.0 ;
- aucun carnet d'ordres ou Level 2 ;
- calcul weekly dérivé localement du daily ;
- réutilisation du cache OHLCV quotidien déjà nécessaire ;
- suppression de l'ancien cache 5 minutes restauré lorsqu'il existe.

Le benchmark global conserve par ailleurs le full Committee lourd à une cadence hebdomadaire et l'ETF MT standalone en mode diagnostic/on-demand.

La mesure de référence à maintenir est : minutes runner, appels API, taille caches/artefacts, fraîcheur, couverture et stabilité des décisions.

## 6. Validation et historique du chantier

### 6.1 Baseline V21.8.1

- Full audit GitHub Actions #32119662770 : **SUCCESS**.
- ETF MT validation #32119662757 : **SUCCESS**.
- Aucun déverrouillage du holdout.
- Aucun ordre réel.

### 6.2 V24.2.x — expérimentation abandonnée

Les PR historiques #79/#80 puis les corrections associées ont démontré que le code intraday pouvait être isolé techniquement, mais cette direction ne correspond pas au besoin fonctionnel final : l'objectif est une aide TCT quotidienne/hebdomadaire, pas un système de day trading/scalping.

Conséquences :

- V24.2.x est retirée du runtime actif ;
- ses caches, ledgers, runners, analytics et tests spécifiques sont supprimés du HEAD ;
- ses résultats éventuels ne doivent jamais être utilisés comme preuve de performance ;
- l'historique Git est conservé uniquement pour traçabilité.

### 6.3 V24.3.0 — statut

V24.3.0 est un challenger `SHADOW_RESEARCH_ONLY` sans autorité de production.

Avant promotion éventuelle, il devra démontrer en PIT/OOS un gain robuste sur :

- espérance par trade ;
- taux positif ;
- gain moyen/perte moyenne ;
- profit factor ;
- drawdown et queue de pertes ;
- qualité des entrées ;
- faux breakouts évités ;
- valeur des retests ;
- performance selon RVOL/volatilité ;
- contribution de l'alignement weekly ;
- pertes évitées ou réduites via les warnings de sortie.

Le taux de réussite seul ne suffit pas. Aucun score, poids ou seuil V24.3.0 n'est promu avant cette validation.

## 7. Runbook opérationnel

### Chaque jour ouvré

1. Exécuter `PEA Daily Collect + TCT CT V21.8.1` après clôture.
2. Vérifier audit collecte, cache/provenance, qualité des données et synthèse Android TCT/CT canonique.
3. Vérifier séparément `TCT_DAILY_TRADER_V24_3_0_AUDIT.json` et `ANDROID_TCT_DAILY_TRADER_SHADOW.md`.
4. Traiter V24.3.0 uniquement comme information SHADOW.
5. En cas d'échec V24.3.0 : conserver le run canonique s'il est vert et corriger uniquement la cause SHADOW démontrée.
6. En cas d'échec canonique : corriger la cause démontrée sans déclencher les modules lourds par défaut.

### Chaque vendredi

1. Exécuter `PEA Weekly Heavy Committee V21.8.1`.
2. Vérifier quality gates, Comité, ETF MT, Gold, IPO, Sector/Theme, Risk, Entry/Exit V21.8, audit des critères.
3. Publier Android Comité complet et restitution PC explicative.

### Release / audit

Un full run ou PIT/OOS n’est déclenché que pour un changement runtime matériel, une release, une non-régression planifiée ou une question statistique nécessitant cette validation.

Pour V24.3.0, aucune optimisation opportuniste des poids/seuils n'est autorisée sur les premiers résultats. Une hypothèse candidate doit être gelée avant validation OOS.

## 8. Règle WIP=1

Le processus reste WIP=1 : une seule amélioration active à la fois. Toute nouvelle idée est mise en file d'attente jusqu'à clôture auditée du chantier courant.

**Chantier actif au 19/08/2026 : TCT V24.3.x Daily/Weekly Trader Tools.**

Le CT reste gelé pendant cette phase. La production V21.8.1 reste la référence tant qu'une nouvelle version n'a pas satisfait la Definition of Done : audit, correction, intégration, tests, validation PIT/OOS pertinente, run représentatif et synchronisation documentaire.
