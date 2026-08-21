# Audit Actions CT — V22.0.0

Date : 21/08/2026

## 1. Conclusion de l'audit initial

Le module Actions CT existant n'était pas à remplacer directement par le référentiel V21.4. L'analyse du code et de la gouvernance montre que :

- le runner quotidien utilise volontairement `V21_ACTIONS_REFERENCE_V21_0.json` comme baseline ;
- `V21_ACTIONS_CRITERIA_REGISTRY.json` V21.4 est explicitement un challenger non optimisé PIT ;
- l'audit V21.8.1 avait mesuré environ **52,98 %** de couverture pondérée du challenger Actions CT ;
- V21.8.1 indique que les modèles CT/MT complets ne sont pas encore certifiés PIT ;
- T1/T2 sont strictement `ACTION_TCT_ONLY`.

La bonne correction n'est donc pas une substitution brutale de V21.0 par V21.4, mais la création d'un challenger CT indépendant, causal et mesurable.

## 2. Défauts corrigés

### A. Sélection et timing insuffisamment séparés

**Avant :** le score CT de sélection et la gouvernance générique V21.8 fournissaient une aide au timing, mais sans moteur CT daily/weekly dédié.

**Correction :** V22.0 conserve le score baseline et calcule séparément un `entry_score` CT avec ses propres gates et états SHADOW.

### B. Pas de confluence CT daily/weekly dédiée

**Correction :** ajout de tendance daily 20/50/120, momentum 10/20/60, weekly 10/20 semaines et momentum weekly 4/8 semaines, en excluant la semaine incomplète des statistiques terminées.

### C. Cassures non mémorisées dans un moteur CT spécifique

**Correction :** mémoire breakout 55/120 jours et retest sur 10 séances, avec détection de failed breakout.

### D. Risque d'entrée trop peu explicite

**Correction :** gates liquidité, weekly adverse, conflit avec risque sortie, distance d'invalidation, sur-extension SMA20 en ATR et gap excessif.

### E. Rotation sectorielle potentiellement trompeuse

**Correction :** la force sectorielle devient un bloc de confirmation et non une justification autonome. Un warning `SECTOR_HOT_VALUATION_RISK` est produit lorsque le secteur est fort mais la valorisation est tendue.

### F. Risque événementiel

**Correction :** warning spécifique lorsque les résultats sont attendus à moins de deux jours.

### G. Sorties CT insuffisamment structurées

**Correction :** score de risque sortie dédié avec rupture de tendance, momentum, weekly, distribution, détérioration relative et volatilité.

Un `EXIT_RISK_HIGH_SHADOW` requiert une confirmation temporelle issue d'une séance antérieure.

### H. Rejouabilité des runs

**Défaut découvert pendant l'itération V22.0 :** une première implémentation de l'état de sortie aurait pu considérer un rerun le même jour comme confirmation temporelle.

**Correction :** l'état persistant contient désormais `snapshot_date`. Une confirmation n'est valide que si la date précédente est strictement antérieure à la date courante. Un rerun même jour est idempotent.

### I. Absence de preuve forward-PIT CT dédiée

**Correction :** création d'un ledger PIT immuable avec fingerprint SHA-256 des champs décisionnels, première observation conservée, fail-closed en cas de mutation et outcomes 10/20/40 séances.

## 3. Ce qui n'a volontairement pas été modifié

- les poids de la baseline V21.0 ;
- les seuils de production CT ;
- V21.8.1 ;
- TCT V24.4.1 ;
- les formules T1/T2 ;
- ETF ;
- sizing ;
- take-profit ;
- stop-loss de production ;
- holdout final ;
- ordres réels.

## 4. Validation pré-enregistrée

Le challenger V22.0 devra être évalué sur des snapshots réellement produits au fil du temps. Aucun backfill synthétique de snapshots historiques n'est admis.

Maturité minimale : 300 observations étiquetées à 20 séances, 50 ISIN, 20 dates et 40 entrées READY/STRONG.

Comparaison contre la baseline V21.0 au même snapshot :

- win rate : +5 points minimum ;
- rendement médian 20 séances : +0,5 point minimum ;
- Spearman score/rendement : >= 0,10 ;
- faux positifs : <= 45 % ;
- dégradation MAE médiane : <= 0,5 point.

## 5. Statut de version

`ACTION_CT_V22.0.0_DAILY_WEEKLY_CONFLUENCE_SHADOW`

Statut : **SHADOW_RESEARCH_ONLY**.

Cette version est suffisamment structurée pour être exécutée quotidiennement et accumuler la preuve PIT, mais elle n'est pas autorisée à remplacer la baseline avant maturité, CI verte, validation PIT/OOS et décision explicite de promotion.
