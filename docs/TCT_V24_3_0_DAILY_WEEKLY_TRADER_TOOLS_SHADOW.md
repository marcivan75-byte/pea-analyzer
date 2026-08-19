# TCT V24.3.0 — Daily / Weekly Trader Tools SHADOW

Date : 19/08/2026

## 1. Objet

V24.3.0 enrichit le module ACTION TCT avec des outils de lecture et de discipline utilisés par les traders court terme, **sans transformer le système en day trading**.

Horizon visé : quelques séances, avec lecture quotidienne prioritaire et confirmation hebdomadaire.

Le module n'utilise que les données OHLCV quotidiennes déjà collectées par PEA Analyzer. Les données hebdomadaires sont dérivées de ces historiques quotidiens.

Sont explicitement exclus :

- données 1 minute / 5 minutes / intraday ;
- quasi temps réel ;
- carnet d'ordres et order flow live ;
- spread live ;
- VWAP de séance ;
- opening range intraday ;
- nouveaux abonnements ou téléchargements de données nécessaires à ce challenger.

## 2. Principe

Le TCT existant reste responsable de la sélection et T1/T2 reste réservé au module TCT.

V24.3.0 ajoute ensuite un diagnostic SHADOW destiné à répondre à deux questions :

1. **L'entrée est-elle techniquement bien préparée à l'échelle daily/weekly ?**
2. **Une position TCT déjà ouverte présente-t-elle des signes de détérioration justifiant une surveillance de sortie ?**

La chaîne de recherche devient :

`Univers PEA ACTION → TCT → T1/T2 → outils trader daily/weekly → diagnostic entrée/sortie SHADOW`.

## 3. Outils de traders transposés au quotidien

### 3.1 Breakout et retest

Le moteur observe les plus hauts précédents sur 20 et 55 séances.

Il distingue :

- proximité d'une résistance ;
- breakout 20 jours ;
- breakout 55 jours ;
- retour/retest d'un breakout récent.

Le retest est particulièrement utile pour éviter d'acheter une cassure déjà trop éloignée de son niveau technique.

### 3.2 Volume relatif et accélération

Les concepts de RVOL des day traders sont transposés en daily :

- volume du jour / médiane des 20 séances précédentes ;
- accélération du volume moyen récent par rapport au volume 20 jours ;
- turnover quotidien médian pour contrôler la liquidité réelle du titre.

Aucun volume intraday n'est requis.

### 3.3 Volatilité exploitable

Le moteur utilise :

- ATR 14 jours ;
- range quotidien par rapport au range médian ;
- détection d'une compression récente ;
- expansion après compression.

L'objectif n'est pas de rechercher une volatilité maximale mais une volatilité compatible avec un mouvement TCT exploitable.

### 3.4 Qualité de la clôture et price action

La bougie quotidienne est analysée avec :

- position de la clôture dans le range ;
- corps de bougie ;
- mèches haute et basse ;
- direction ouverture/clôture ;
- gap par rapport à la clôture précédente, exprimé également en unités d'ATR.

Un gap ou une extension excessifs peuvent déclencher `WAIT_PULLBACK_SHADOW` même lorsque le momentum est favorable.

### 3.5 Tendance et momentum

La couche observe notamment :

- EMA 9 ;
- EMA 20 ;
- pente EMA 9 ;
- performance 5 jours ;
- performance 20 jours.

Ces éléments servent à confirmer ou à invalider la dynamique TCT, jamais à décider seuls.

### 3.6 Prix pondéré par le volume — version daily

V24.3.0 calcule un **prix roulant pondéré par le volume sur 20 et 60 séances** à partir des données quotidiennes.

Il ne s'agit pas d'un VWAP intraday de séance. C'est une approximation daily destinée à répondre à une question de trader : le titre se situe-t-il au-dessus ou en dessous du prix moyen auquel le volume récent s'est échangé ?

L'écart au prix pondéré 20 jours est également exprimé en ATR afin de détecter une sur-extension.

### 3.7 Niveaux pivots et niveaux clés

À partir des données daily/weekly existantes, le module publie :

- pivot de la séance précédente ;
- R1 et S1 de la séance précédente ;
- plus haut et plus bas de la semaine précédente ;
- pivot de la semaine précédente ;
- niveau de breakout/retest ;
- référence d'invalidation structurelle.

Ces niveaux donnent au Comité des zones d'entrée, de confirmation et d'invalidation sans nécessiter de suivi intraday.

### 3.8 Confirmation hebdomadaire

Le weekly est construit à partir du daily existant :

- tendance par moyenne 10 semaines ;
- momentum 4 semaines ;
- position de la clôture dans le range hebdomadaire ;
- niveaux de la semaine précédente.

Le weekly joue un rôle de filtre de contexte et de robustesse, cohérent avec un TCT pouvant durer plusieurs séances.

## 4. Diagnostic d'entrée SHADOW

Baseline pré-enregistrée :

- structure breakout/retest : 20 % ;
- volume/liquidité : 15 % ;
- qualité de clôture : 15 % ;
- volatilité : 15 % ;
- momentum/tendance : 15 % ;
- alignement weekly : 15 % ;
- position vs prix pondéré volume : 5 %.

États initiaux :

- `ENTRY_STRONG_SHADOW` ;
- `ENTRY_READY_SHADOW` ;
- `WAIT_PULLBACK_SHADOW` ;
- `LIQUIDITY_WARNING_SHADOW` ;
- `WAIT_SHADOW` ;
- `DATA_INSUFFICIENT`.

Ces poids et seuils sont des hypothèses de recherche gelées avant validation. Ils n'ont aucune influence sur la décision de production.

## 5. Diagnostic de sortie / risque SHADOW

Le score de détérioration examine :

- échec de breakout / rupture structurelle ;
- passage sous EMA 9 / EMA 20 ;
- séance de distribution avec volume ;
- détérioration du momentum ;
- détérioration weekly ;
- expansion baissière de volatilité.

États :

- `HOLD_SUPPORTIVE_SHADOW` ;
- `EXIT_WATCH_SHADOW` ;
- `EXIT_RISK_HIGH_SHADOW` ;
- `DATA_INSUFFICIENT`.

Il n'existe aucun take-profit fixe ni stop-loss fixe V24.3. La référence d'invalidation structurelle est un champ de recherche, pas un ordre.

## 6. Warnings principaux

Le challenger peut signaler :

- sur-extension vs prix pondéré volume 20 jours ;
- gap excessif vs ATR ;
- liquidité quotidienne insuffisante ;
- invalidation structurelle trop éloignée ;
- failed breakout ;
- distribution avec volume.

Le plafond de 7 % reste uniquement un plafond de recherche sur la distance d'invalidation structurelle ; ce n'est pas un stop automatique.

## 7. Données et coût

V24.3.0 ne déclenche **aucun téléchargement de données de marché supplémentaire**.

Il réutilise `data/cache/actions`, le cache OHLCV quotidien déjà nécessaire au TCT/T1/T2.

Le workflow supprime l'ancien répertoire `data/cache/actions_intraday_5m` lorsqu'il est encore restauré depuis un cache historique, afin de ne plus perpétuer ce stockage inutile.

## 8. Gouvernance

- `SHADOW_RESEARCH_ONLY` ;
- influence décision = 0 ;
- influence score = 0 ;
- influence sizing = 0 ;
- influence stop = 0 ;
- influence CT = 0 ;
- aucun ordre réel ;
- holdout fermé ;
- aucun retuning automatique ;
- aucune autorité de promotion.

Le CT reste gelé jusqu'à la clôture auditée du chantier TCT.

## 9. Abandon de V24.2.x intraday

Les modules V24.2.x intraday/5 minutes ont été développés à partir d'une interprétation trop littérale de l'inspiration day trading. Ils ne correspondent pas au besoin fonctionnel final et sont retirés du runtime actif.

Ils restent uniquement dans l'historique Git pour traçabilité. Aucun résultat V24.2.x ne doit être utilisé pour justifier une performance ou une décision.

## 10. Validation future

Avant toute promotion, V24.3.0 devra être comparé à la baseline TCT sur des observations PIT/OOS avec notamment :

- espérance par trade ;
- taux de réussite ;
- gain moyen / perte moyenne ;
- profit factor ;
- drawdown et queue de pertes ;
- résultat par setup breakout/retest ;
- résultat selon RVOL et volatilité ;
- résultat selon alignement weekly ;
- valeur ajoutée sur le timing d'entrée ;
- valeur ajoutée sur les sorties / pertes évitées.

Le taux de réussite seul ne constitue pas un critère suffisant de promotion.
