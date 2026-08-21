# Référentiel TCT V24.4.2 — FINAL SHADOW

Date : 21/08/2026  
Périmètre : Actions PEA — Très Court Terme  
Horizon : quelques séances à environ une semaine  
Statut : `SHADOW_RESEARCH_ONLY`  
Production canonique : V21.8.1 inchangée  
Epoch de preuve : `V24.4.2_ONLY_NO_MIX_WITH_PRIOR_EPOCHS`

Ce document est la **source humaine unique des valeurs numériques TCT V24.4.2**. Le CDC décrit les exigences et interfaces et renvoie ici au lieu de dupliquer poids et seuils. Les JSON `config/TCT_V24_4_2_*.json` constituent la représentation machine correspondante.

## 1. Gouvernance non négociable

- T1/T2 : ACTION TCT uniquement.
- Aucun transfert automatique vers CT ou ETF.
- Aucun day trading, 1m, 5m, polling continu, Level 2 ou order flow live.
- OHLCV quotidien comme donnée de marché principale ; weekly dérivé localement.
- Deux snapshots catalysts maximum par jour ouvré : PREOPEN et POSTMARKET.
- Aucun ordre réel ; aucun take-profit fixe ; aucun stop-loss fixe promu.
- Influence décision, score production, sizing, stop et CT de V24.3.1/V24.4.2 : `0`.
- Holdout final fermé.
- Aucun retuning automatique.
- Toute modification sémantique future du score ou des labels crée une nouvelle epoch PIT.

## 2. Hiérarchie

1. V21.8.1 : production canonique Entry/Exit.
2. V24.1.7 : baseline TCT et timing exact T1/T2.
3. V24.3.1 : Daily/Weekly Trader Tools SHADOW.
4. V24.4.2 : Next-Session Catalyst Cycle SHADOW.
5. V24.4.2 PIT/OHLC : preuve prospective avant toute promotion.

V24.4.0 et V24.4.1 restent des versions historiques et leurs observations ne sont pas mélangées à l'epoch V24.4.2.

## 3. V24.3.1 Daily/Weekly — paramètres gelés

### 3.1 Entrée

| Bloc | Poids |
|---|---:|
| Structure breakout/retest | 20 % |
| Volume/liquidité | 15 % |
| Price action | 15 % |
| Volatilité | 15 % |
| Momentum | 15 % |
| Weekly | 15 % |
| Prix roulant pondéré volume | 5 % |

Seuils : ENTRY_READY 70 ; ENTRY_STRONG 80 ; couverture minimale 85 % ; confirmations minimum 2/3 ; weekly alignment 65 ; weekly adverse 35 ; risque sortie maximum compatible entrée 50 ; RVOL 1,20 ; accélération volume 1,15 ; gap excessif 1,25 ATR ; overextension 1,75 ATR ; distance d'invalidation recherche 7 % ; turnover médian recherche 500 000 EUR.

### 3.2 Sortie

Poids : failed breakout/structure 25 % ; rupture tendance rapide 20 % ; distribution volume 15 % ; momentum 15 % ; weekly 15 % ; volatilité adverse 10 %.

Seuils : EXIT_WATCH 50 ; EXIT_RISK_HIGH 70. `EXIT_RISK_HIGH_SHADOW` exige une confirmation structurelle.

Ces valeurs restent a priori tant que la preuve PIT n'autorise pas une revue. Elles ne sont pas retunées par V24.4.2.

## 4. V24.4.2 — sélection des candidats

Maximum : **60** candidats par snapshot.

### 4.1 Score interne de priorité

- entrée V24.3.1 : 25 % ;
- risque sortie : 20 % ;
- news déjà connue : 20 % ;
- proximité résultats : 15 % ;
- volatilité ATR : 10 % ;
- qualité T1/T2 : 10 %.

T1/T2 intervient uniquement parce que la sélection est ACTION TCT ; aucun autre horizon n'en hérite.

### 4.2 Quotas anti-dominance

Le sélecteur réserve au maximum, dans l'ordre puis sans doublons :

- 15 sièges ENTRY_READY/ENTRY_STRONG ;
- 10 sièges résultats à <=7 jours ;
- 10 sièges news existante >=65 ;
- 10 sièges ATR élevé ;
- 5 sièges risque sortie élevé ;
- les sièges restants sont remplis par le score composite.

Chaque candidat publie `candidate_rank`, `candidate_priority_score` et `candidate_rank_reason`.

## 5. News catalysts

### 5.1 Source et fenêtre

Source primaire active : GDELT. Les articles doivent avoir un timestamp parsable et appartenir strictement à la fenêtre PIT. Zéro article après requête réussie = observation news nulle valide ; erreur source = donnée manquante.

Cache exact-window : TTL **2 heures**, clé cryptographique requête + début + fin. Le cache ne change jamais la fenêtre causale.

Parallelisme : 6 workers par défaut, plage autorisée 4–8, vagues de 15, délai de départ 0,12 s. Les audits publient p50/p95, erreurs, cache hits et circuit-breaker.

Contrat de source secondaire : prévu mais **non activé avant qualification formelle** du provider secondaire. Une future activation exige timestamp PIT fiable, provenance persistée, tests et nouvelle revue ; elle ne doit pas être simulée.

### 5.2 Classification

Les patterns sont externalisés dans `TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json`. Le classifieur calcule une confiance de match et applique un garde de négation. Exemple : « denies fraud investigation » ne peut pas devenir `FRAUD_INVESTIGATION`.

Catalogue PEA enrichi : PROFIT_WARNING, PARTIAL_PROFIT_WARNING, GUIDANCE_CUT/RAISED, EARNINGS_BEAT/MISS, BANKRUPTCY_DEFAULT, FRAUD_INVESTIGATION, REGULATORY_APPROVAL/REJECTION, TENDER_OFFER, MA_ACQUISITION, TRADING_SUSPENSION, MAJOR_CONTRACT, PRIVATE_PLACEMENT, CAPITAL_RAISE_DILUTION, INDEX_INCLUSION/EXCLUSION, CONSENSUS_REVISION_UP/DOWN, DIVIDEND_CUT, BUYBACK_DIVIDEND_RAISE, ANALYST_UPGRADE/DOWNGRADE, CEO_DEPARTURE, OTHER_NEWS.

Les magnitudes/directions machine sont dans le JSON. Principes clés : OPA/tender offer = amplitude très forte et biais positif ; acquisition générique = amplitude forte mais direction 0 ; suspension = amplitude forte mais direction 0 ; placement privé = biais dilutif négatif ; index inclusion/exclusion = biais modéré +/- ; fraude n'est reconnue que hors négation explicite.

## 6. Score potentiel de mouvement

Poids inchangés afin de ne pas inventer une calibration :

- news magnitude : **45 %** ;
- impulsion technique V24.3.1 : **25 %** ;
- choc marché global : **15 %** ;
- proximité événement planifié : **15 %**.

Couverture minimale pour exposer un score : **70 %**. En dessous : `DATA_DEGRADED_SHADOW` et aucun score de mouvement exploitable.

Seuils : fort potentiel 70 ; potentiel moyen 50.

## 7. Score directionnel

- direction news : **55 %** ;
- direction technique : **25 %** ;
- inverse risque de sortie : **10 %** ;
- risk-on global : **10 %**.

Couverture minimale : **70 %**. Biais haussier >= +25 ; biais baissier <= -25. Si couverture insuffisante, aucune étiquette UP/DOWN n'est admise.

`TECHNICAL_ONLY_SHADOW` reçoit un flag actionnable SHADOW uniquement si impulsion technique >=70 et ATR >=4 %.

## 8. Contexte global — Europe renforcée

Risk-on de base avant overlay futures :

- S&P 500 15 % ;
- Nasdaq 15 % ;
- Russell 2000 10 % ;
- Nikkei 10 % ;
- Euro Stoxx 50 **20 %** ;
- CAC 40 **5 %** ;
- DAX **5 %** ;
- VIX inverse 20 %.

Le bloc Europe représente **30 %** du score de base. PREOPEN : overlay futures S&P/Nasdaq **15 %** maximum sur le risk-on calculé. Intervalle marché : `1d`, jamais intraday.

Les rendements VIX, EuroStoxx50, CAC40 et DAX sont persistés dans chaque prédiction V24.4.2 pour les analyses de stabilité.

## 9. Définition métier du mouvement significatif

Un mouvement J+1 est significatif si l'extrême absolu de séance par rapport à la clôture de référence atteint :

`max(2,0 % ; 1,25 × ATR14 %)`.

Label principal d'amplitude : `realized_session_abs_extreme_pct` = maximum de l'excursion absolue high/low J+1 par rapport à la clôture de référence.

Label secondaire de clôture : `realized_abs_return_pct`.

Cette définition complète les métriques relatives Top10/lift/Spearman par une cible métier absolue.

## 10. PIT OHLC J+1

Le daily cache existant alimente un ledger local sans téléchargement supplémentaire contenant open/high/low/close. Pour chaque PREOPEN, l'outcome est la **première séance réellement observée** dont la date est strictement postérieure à `as_of_date`.

Labels :

- `realized_open_gap_pct` ;
- `realized_session_range_pct` ;
- `realized_high_excursion_pct` ;
- `realized_low_excursion_pct` ;
- `realized_session_abs_extreme_pct` ;
- `realized_max_adverse_excursion_pct` ;
- `realized_close_to_close_return_pct` ;
- `realized_abs_return_pct` ;
- `significant_move_threshold_pct` ;
- `significant_session_move_flag` ;
- `significant_close_move_flag`.

Minimum **80 %** des candidats d'un même snapshot doivent disposer d'un vrai outcome J+1 avant exposition au validateur.

Fingerprint : `TCT_PIT_SHA256_CANONICAL_V3` sur champs prédictifs fixes. Mismatch = fail-closed.

## 11. Validation V24.4.2

Maturité minimale : 60 observations PREOPEN étiquetées ; 20 ISIN ; 15 alertes score >=70 ; 20 appels directionnels ; 15 séances.

Comparateur : `technical_impulse_score` / `technical_direction_score` V24.3.1.

Pour l'amplitude, au moins 2 des 3 gains suivants : Recall Top10 +10 points ; lift décile +0,15 ; Spearman +0,10. Faux fort potentiel <=60 %.

Direction : hit rate >=55 % et non-dégradation vs technique seule.

Diagnostics supplémentaires : précision/recall du mouvement significatif, gap, range, MAE, stabilité secteur, régime risk-on/off, quintile de variation VIX lorsque l'échantillon le permet, et `candidate_rank_reason`.

Une tranche de stabilité est qualifiée à partir de 8 observations. Dégradation directionnelle tolérée : maximum -10 points vs technique dans une tranche qualifiée. La stabilité demeure une **revue manuelle**, jamais une promotion automatique.

## 12. Budgets d'exécution

- PREOPEN : **480 secondes** end-to-end.
- POSTMARKET : **600 secondes**.
- News : maximum 80 % du budget de phase.

Le fetch news fonctionne par vagues et active un circuit-breaker en cas d'épuisement du budget. Le dépassement du budget global est audité et dégrade la qualité plutôt que d'inventer un score complet.

## 13. Calibration

Les poids actuels restent gelés. Le script `tct_v24_4_2_weight_calibration.py` est **offline research only** : il ne s'exécute pas dans le workflow et ne modifie jamais le JSON actif. Il refuse de proposer un candidat avant maturité minimale. Même après maturité, toute pondération candidate reste descriptive ; son adoption créerait une nouvelle epoch pré-enregistrée.

Les seuils ENTRY/EXIT V24.3.1 feront également l'objet d'une revue de quantiles après accumulation suffisante, sans retuning automatique.

## 14. Golden-set métier

Un jeu de régression de >=15 titres de news couvre anglais/français : profit warning, guidance, résultats, fraude avec négation, OPA, suspension, placement privé, inclusion/exclusion d'indice, consensus, réglementation et contrat. Tout changement du classifieur doit maintenir ces invariants ou documenter explicitement une nouvelle sémantique.

## 15. Sorties décisionnelles

Le mobile V24.4.2 doit présenter : phase/couverture/runtime ; Top5 potentiel ; Top haussiers ; conflits news/tech ; EXIT_RISK_HIGH du seed ; lignes dégradées ; circuit-breaker.

Les CSV restent exhaustifs. Les audits JSON publient version, temps, couverture, erreurs, latence, cache, contexte global et gouvernance.

## 16. Statut des findings de l'audit externe

- F01 mutation runner : **corrigé** par injection de dépendances ; V24.4.1 lui-même est refactoré sans monkey-patch.
- F02 GDELT unique : **partiellement corrigé** : cache, télémétrie, circuit-breaker et contrat fallback ; provider secondaire actif différé jusqu'à qualification.
- F03 classification lexicale naïve : **corrigé** par patterns config, négation, confiance et golden-set ; NLP reste recherche future.
- F04 poids a priori : **encadré** par calibration offline non appliquée ; aucune prétendue optimisation sans PIT.
- F05 Top60 peu discriminatif : **corrigé** par quotas + score + rank_reason.
- F06 outcome close-only : **corrigé** par OHLC multi-label J+1.
- F07 CDC/référentiel redondants : **corrigé** : présent Référentiel = source numérique unique.
- F08 workers/rate-limit non profilés : **corrigé** : configuration + p50/p95 + vagues.
- F09 force relative secteur : **différé** jusqu'à univers sectoriel PIT stable ; aucun poids arbitraire ajouté.
- F10 stabilité : **corrigé** par diagnostics/gates secteur/régime/VIX.
- F11 cache news : **corrigé** par cache exact-window TTL 2h.
- F12 mobile peu actionnable : **corrigé** par template Top5/conflits/qualité.

## 17. Ce que V24.4.2 ne prétend pas démontrer

Ces corrections améliorent la robustesse logique, la couverture des labels, la maintenabilité et l'observabilité. Elles **ne prouvent pas encore une hausse de performance financière**. La performance prédictive devra être démontrée par la nouvelle epoch forward-PIT V24.4.2, puis PIT/OOS selon la gouvernance.
