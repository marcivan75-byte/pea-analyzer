# Audit final PEA Analyzer — constats et plan de correction

Date : 2026-08-18

## Statut de gouvernance

- P0 à P5 sont clôturés selon WIP=1.
- Baseline Entry/Exit : V21.8.1.
- T1/T2 : ACTION TCT uniquement.
- Holdout final : fermé.
- Aucun ordre réel.
- Aucune modification de poids/seuil n'est autorisée sans preuve PIT/OOS robuste.

## 1. Critères et pondérations — constat sur artefact réel

Source de mesure : artefact du run `main` #32064095330, 11 486 décisions, fichiers `CRITERIA_COVERAGE.csv` et `EFFECTIVE_WEIGHTS_100.csv`.

### Actions

Couverture pondérée approximative des critères actifs du challenger actuel :

- CT : 52,98 %
- MT : 44,71 %
- LT : 48,49 %
- SHORT : 58,32 %
- TOP_DOWN : 45,36 %

Lacunes matérielles observées :

- `net_upgrades_30d_v21` : 0 % sur plusieurs horizons ;
- `earnings_catalyst_score` : 0 % en CT ;
- `morningstar_action_score` : 0 % ;
- `broker_weighted_revision_30d` : 0 % en MT ;
- `funnel_global_news_score` : 0 % ;
- `news_catalyst_score` : quasi absent ;
- consensus / target / forward PER : couverture faible, proche de 7 % sur plusieurs champs.

Conséquence : priorité à l'enrichissement/provenance et non à une nouvelle optimisation des poids. La décision Actions finale reste la référence V21.0 gelée ; le challenger enrichi reste shadow tant qu'un PIT/OOS dédié ne prouve pas son uplift.

### ETF

Couverture pondérée approximative :

- CT : 89,28 %
- LT : 70,01 %
- SHORT : 76,84 %
- TOP_DOWN : 72,90 %

Lacunes principales :

- `fund_total_assets_eur_m` : ~0,98 % ;
- `ter_pct` : ~10,78 % ;
- `sentiment_regime_score` : 0 % en SHORT ;
- plusieurs scores news TOP_DOWN : 0 %.

Le cœur ETF MT V20.8.1 38 critères conserve seul son attribution historique 90,91 %. Aucun élargissement de cette attribution n'est autorisé.

## 2. Audit des pondérations

Les gates actuels contrôlent déjà la somme des poids à 100 % pour les horizons Actions et les contrats ETF/Gold/IPO/Sector/Risk.

Constat important : `V21_ACTIONS_CRITERIA_REGISTRY.json` contient des poids de challenger pour des overlays encore non validés (Morningstar Action, Sector Rotation, catch-up, renforcements >4 %). Le runtime final remet les décisions Actions sur la référence V21.0 avant publication. Il n'y a donc pas de promotion silencieuse en production, mais la séparation ACTIVE / SHADOW / CONTEXT_ONLY doit être rendue explicite dans le référentiel et la restitution PC.

Décision : **aucun changement de poids dans cette phase**. D'abord restaurer la couverture des critères à forte valeur et construire un historique PIT suffisant ; ensuite seulement comparer baseline/challenger en OOS.

## 3. Restitution Comité d'Investissement — gap démontré

### Android

Le workflow quotidien publie actuellement dans le GitHub Job Summary uniquement `RISK_V1_1_CONTROL_CENTER.md`. Il n'existe pas dans `main` de Control Center Android complet du Comité regroupant Actions, ETF, TCT, Entry/Exit, qualité des données et warnings sectoriels.

### PC

`EFFECTIVE_WEIGHTS_100.csv` expose les poids théoriques/effectifs et la disponibilité par titre, mais le moteur ne persiste pas aujourd'hui, pour chaque critère de chaque titre sélectionné, toutes les informations nécessaires à une explication complète : valeur brute, score normalisé du critère, contribution pondérée exacte, provenance/fraîcheur et statut ACTIVE/SHADOW/CONTEXT_ONLY.

Décision : créer une couche d'explicabilité canonique issue du même run que `COMMITTEE_DECISIONS.csv`, sans recalcul approximatif ni donnée inventée. Si une contribution ne peut pas être reconstruite exactement, elle doit être marquée `NOT_PERSISTED` jusqu'à modification du moteur de scoring.

## 4. GitHub Actions — gap coût/performance démontré

Le workflow `committee_master_daily.yml` reste planifié chaque jour ouvré et exécute une chaîne monolithique :

1. collecte/refresh ;
2. structure ETF ;
3. ETF MT ;
4. Gold ;
5. IPO ;
6. Sector Rotation ;
7. Committee ;
8. Risk ;
9. reporting/artefacts.

Timeout : 180 minutes.

Le workflow possède déjà deux bons garde-fous : `concurrency/cancel-in-progress` et des `paths` ciblés sur push. Mais la fréquence planifiée reste identique pour les modules sensibles au timing et les modules lourds à inertie plus forte.

## 5. Architecture cible à valider

### COLLECT_DAILY

Une collecte quotidienne unique, avec provenance, TTL et cache par source/champ/instrument. Une donnée fraîche validée n'est pas recollectée.

### TCT_CT_DAILY

Calcul quotidien limité aux horizons TCT/CT et aux dépendances strictement nécessaires. Publication Android quotidienne et mise à jour PC uniquement pour les titres impactés.

### WEEKLY_HEAVY

Une fois par semaine : MT/LT, ETF MT, Sector/Theme, Risk portefeuille/bêta, IPO, agrégations transverses et Comité complet PC.

### EVENT_RECOMPUTE

Recalcul ciblé hors cadence hebdomadaire uniquement si événement matériel : résultats, profit warning, consensus majeur, événement géopolitique, anomalie qualité.

### RELEASE_AUDIT

Full run, PIT/OOS et non-régression uniquement quand un changement runtime matériel ou une release le justifie.

## 6. Mesures obligatoires avant promotion de l'architecture

Comparer ancienne et nouvelle architecture sur :

- minutes runner/jour et semaine ;
- appels API ;
- taux de cache hit ;
- volume d'artefacts ;
- fraîcheur par famille de données ;
- couverture par critère ;
- stabilité des scores/décisions ;
- absence de perte de couverture ;
- absence de dégradation statistique TCT/CT/MT/ETF.

## 7. Ordre de correction WIP=1 de l'audit final

1. explicabilité et restitution CI Android/PC issue du run canonique ;
2. audit exécutable ACTIVE / SHADOW / CONTEXT_ONLY et couverture des critères ;
3. enrichissement des critères manquants à forte valeur, avec priorité aux sources déjà identifiées et PIT-safe ;
4. séparation collecte / calculs TCT-CT / calculs lourds ;
5. benchmark coût/performance avant-après ;
6. documentation/runbook/process final unique ;
7. seulement ensuite, étude de nouvelles pondérations si l'historique PIT/OOS le permet.
