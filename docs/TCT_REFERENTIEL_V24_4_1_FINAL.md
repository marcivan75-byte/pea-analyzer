# Référentiel TCT V24.4.1 — FINAL SHADOW

Date : 21/08/2026  
Périmètre : Actions PEA — Très Court Terme (TCT)  
Horizon fonctionnel : quelques séances à environ une semaine  
Nature : aide à la décision, aucun ordre réel

## 1. Hiérarchie normative

1. Production canonique Entry/Exit : V21.8.1 inchangée.
2. Baseline TCT et timing exact T1/T2 : V24.1.7, ACTION TCT uniquement.
3. Outils Daily/Weekly : V24.3.1 SHADOW, corrigée au 21/08/2026.
4. Anticipation prochaine séance PREOPEN/POSTMARKET : V24.4.1 SHADOW.
5. Validation : forward-PIT V24.4.1 puis PIT/OOS avant toute promotion.

T1/T2 ne doivent jamais être utilisés pour les ETF ou pour un autre horizon sans décision explicite ultérieure.

## 2. Principes non négociables

- Pas de day trading.
- Pas de 1m/5m.
- Pas de polling continu.
- Pas de carnet d'ordres/Level 2 requis.
- Pas de cotations extended-hours individuelles obligatoires pour les actions PEA.
- Daily OHLCV déjà collecté comme donnée principale de marché.
- Weekly calculé localement depuis le daily.
- News horodatées strictement antérieures au snapshot.
- Aucun take-profit fixe opérationnel.
- Aucun stop-loss fixe arbitraire promu par V24.3.1/V24.4.1.
- L'invalidation structurelle est un diagnostic de recherche, pas un ordre automatique.
- Influence production, sizing, stop et CT des couches V24.3.1/V24.4.1 : 0.
- Holdout final fermé.

## 3. Couche Baseline / T1-T2

### 3.1 Rôle

La baseline TCT préexiste aux couches trader-tools et catalysts. Elle détermine l'univers TCT et le timing T1/T2 exact. V24.3.1 et V24.4.1 ne doivent pas recréer l'univers par des règles parallèles.

### 3.2 Gouvernance T1/T2

- Scope : ACTION_TCT_ONLY.
- Même séance que la formation du signal : interdite lorsqu'une information de clôture complète est nécessaire.
- Aucun effet ETF.
- Aucun transfert automatique au CT.
- Holdout fermé.

## 4. Couche V24.3.1 — Daily/Weekly Trader Tools

### 4.1 Objectif

Importer dans une approche quotidienne les outils utiles des traders actifs afin d'améliorer la qualité des entrées et d'identifier plus tôt une détérioration de sortie, sans augmenter la fréquence de cotation.

### 4.2 Données

- OHLCV daily du cache Actions.
- Minimum : 60 barres quotidiennes.
- Weekly : agrégation `W-FRI` depuis le daily.
- Bougie du jour : exclue avant la garde locale de clôture (18:00 Europe/Paris) lorsqu'elle peut être partielle.
- Aucun téléchargement de marché supplémentaire requis par V24.3.1.

### 4.3 Lookbacks

| Élément | Fenêtre |
|---|---:|
| ATR | 14 jours |
| Volume/RVOL | 20 jours |
| Momentum rapide | 5 jours |
| Momentum intermédiaire | 20 jours |
| Breakout rapide | 20 jours |
| Breakout lent | 55 jours |
| Mémoire retest | 5 séances |
| EMA rapide | 9 jours |
| EMA lente | 20 jours |
| Prix pondéré volume roulant | 20 / 60 jours |
| Tendance weekly | 10 semaines |
| Momentum weekly | 4 semaines |

### 4.4 Pondération entrée SHADOW

| Bloc | Poids |
|---|---:|
| Structure breakout/retest | 20 % |
| Volume / liquidité | 15 % |
| Price action / qualité de clôture | 15 % |
| Volatilité | 15 % |
| Momentum | 15 % |
| Alignement weekly | 15 % |
| Prix roulant pondéré volume | 5 % |
| **Total** | **100 %** |

### 4.5 Pondération risque de sortie SHADOW

| Bloc | Poids |
|---|---:|
| Failed breakout / structure | 25 % |
| Rupture tendance rapide | 20 % |
| Distribution avec volume | 15 % |
| Détérioration momentum | 15 % |
| Détérioration weekly | 15 % |
| Volatilité adverse | 10 % |
| **Total** | **100 %** |

### 4.6 Seuils V24.3.1

| Paramètre | Valeur |
|---|---:|
| ENTRY_READY | 70 |
| ENTRY_STRONG | 80 |
| EXIT_WATCH | 50 |
| EXIT_RISK_HIGH | 70 |
| RVOL confirmation | 1,20 |
| Accélération volume confirmation | 1,15 |
| Gap excessif | 1,25 ATR |
| Overextension | 1,75 ATR |
| Plafond recherche distance invalidation | 7 % |
| Turnover médian 20j minimum recherche | 500 000 EUR |
| Couverture entrée minimum | 85 % |
| Confirmations minimum ENTRY_READY | 2 |
| Confirmations minimum ENTRY_STRONG | 3 |
| Weekly alignment minimum | 65 |
| Weekly adverse maximum | 35 |
| Risque sortie maximum compatible entrée | 50 |
| Distribution confirmant sortie | 2 signaux / 3 séances |

### 4.7 Confirmations d'entrée

- STRUCTURE : breakout 20j/55j ou retest valide.
- RVOL : RVOL >= 1,20.
- VOLUME_ACCELERATION : accélération >= 1,15.
- VOLATILITY_EXPANSION : expansion après compression.
- WEEKLY : score weekly >=65.
- PRICE_ACTION : clôture forte, positive et bien située dans le range.

`ENTRY_READY_SHADOW` et `ENTRY_STRONG_SHADOW` exigent à la fois score, nombre de confirmations et au moins un trigger structure/RVOL/volatility expansion.

### 4.8 États d'entrée

- `DATA_INSUFFICIENT`
- `LIQUIDITY_WARNING_SHADOW`
- `ENTRY_CONFLICT_SHADOW`
- `WEEKLY_CONFLICT_SHADOW`
- `WAIT_RISK_SHADOW`
- `WAIT_PULLBACK_SHADOW`
- `ENTRY_STRONG_SHADOW`
- `ENTRY_READY_SHADOW`
- `WAIT_SHADOW`

### 4.9 États de sortie

- `DATA_INSUFFICIENT`
- `EXIT_RISK_HIGH_SHADOW`
- `EXIT_WATCH_SHADOW`
- `HOLD_SUPPORTIVE_SHADOW`

`EXIT_RISK_HIGH_SHADOW` exige un score élevé ET une confirmation structurelle : failed breakout, clôture sous le plus bas précédent ou distribution multi-séances.

### 4.10 Correction 21/08/2026

Un score de structure valide égal à `0.0` reste désormais `0.0`. Le fallback 40 n'est appliqué qu'à une valeur réellement absente (`None`).

## 5. Couche V24.4.1 — Next-Session Catalyst Cycle

### 5.1 Objectif

Classer, avant l'ouverture européenne et après les clôtures, les candidats TCT qui ont la plus forte probabilité d'enregistrer un mouvement significatif lors de la prochaine séance. La probabilité d'amplitude est distincte du sens probable.

### 5.2 Phases

#### PREOPEN

Snapshot unique planifié à 06:40 UTC les jours ouvrés.

Contexte :
- news depuis la dernière vraie clôture européenne ;
- dernière séance US disponible ;
- Nikkei ;
- VIX ;
- futures S&P 500 / Nasdaq en snapshot `1d` ;
- pétrole, or, EUR/USD ;
- contexte technique V24.3.1.

#### POSTMARKET

Snapshot unique planifié à 21:15 UTC les jours ouvrés.

Contexte :
- news depuis la clôture européenne ;
- contexte global après Europe/US ;
- préparation du risque/catalyseur pour la séance suivante.

### 5.3 Candidats

- Maximum : 60 titres TCT.
- Priorisation interne : entrée, risque sortie, news existante, proximité résultats, volatilité.
- Cette priorisation ne constitue pas une décision de production.

### 5.4 News

Source actuelle : GDELT.

Règles :
- timestamp exploitable obligatoire ;
- fenêtre `[dernière clôture réelle ; snapshot]` ;
- déduplication des titres ;
- maximum 25 enregistrements interrogés par candidat ;
- maximum 5 titres de news persistés ;
- classifications anglais/français/allemand sélectionné.

### 5.5 Catalogue de catalyseurs V24.4.1

| Événement | Magnitude | Direction |
|---|---:|---:|
| Profit warning | 100 | -100 |
| Guidance cut | 95 | -95 |
| Guidance raised | 95 | +95 |
| Earnings beat | 90 | +85 |
| Earnings miss | 90 | -85 |
| Bankruptcy/default | 100 | -100 |
| Fraud / investigation explicite sévère | 95 | -90 |
| Regulatory rejection | 95 | -90 |
| Regulatory approval | 85 | +80 |
| M&A / acquisition générique | 90 | **0** |
| Major contract | 78 | +75 |
| Capital raise / dilution | 78 | -70 |
| Dividend cut | 82 | -80 |
| Buyback / dividend raise | 65 | +60 |
| Analyst downgrade | 62 | -60 |
| Analyst upgrade | 58 | +55 |
| CEO departure | 58 | -35 |
| Other news | 35 | 0 |

Une enquête générique sans contexte fraude/comptable/pénal/réglementaire n'est plus classée automatiquement `FRAUD_INVESTIGATION`.

### 5.6 Score de potentiel de mouvement

| Composant | Poids |
|---|---:|
| News magnitude | 45 % |
| Technical impulse V24.3.1 | 25 % |
| Global market shock | 15 % |
| Événement planifié connu | 15 % |
| **Total** | **100 %** |

Correction V24.4.1 : l'événement planifié ne réutilise plus `news_catalyst_score`. Il est actuellement basé sur la proximité des résultats afin d'éviter le double comptage de la news.

### 5.7 Score directionnel

| Composant | Poids |
|---|---:|
| Direction news | 55 % |
| Direction technique | 25 % |
| Inverse du risque de sortie | 10 % |
| Risk-on global | 10 % |
| **Total** | **100 %** |

### 5.8 Couverture fail-closed V24.4.1

- Couverture minimum score mouvement : 70 %.
- Couverture minimum score direction : 70 %.
- Si mouvement <70 % : `movement_potential_score = None` et `DATA_DEGRADED_SHADOW`.
- Si direction <70 % : aucun `UP_CATALYST_SHADOW` / `DOWN_CATALYST_SHADOW`.
- Une requête news réussie sans article vaut une observation news nulle et non une donnée manquante.

### 5.9 Seuils catalysts

| Paramètre | Valeur |
|---|---:|
| Fort potentiel mouvement | 70 |
| Potentiel moyen | 50 |
| Biais haussier | +25 |
| Biais baissier | -25 |
| ATR élevé contexte | 4 % |
| Proximité résultats | 7 jours |

### 5.10 États catalyst

- `DATA_DEGRADED_SHADOW`
- `NEWS_CONFLICT_SHADOW`
- `UP_CATALYST_SHADOW`
- `DOWN_CATALYST_SHADOW`
- `VOLATILITY_ALERT_SHADOW`
- `TECHNICAL_ONLY_SHADOW`
- `NO_CATALYST_SHADOW`

## 6. Contexte marché global

### 6.1 Séances terminées

- S&P 500
- Nasdaq Composite
- Russell 2000
- VIX
- Nikkei 225
- Euro Stoxx 50
- CAC 40
- DAX

### 6.2 Snapshot ponctuel

- S&P 500 future
- Nasdaq future
- pétrole WTI
- or
- EUR/USD

Intervalle : `1d`. Période technique : 5 jours. Aucun polling.

### 6.3 Risk-on

Poids :
- S&P 500 : 25 %
- Nasdaq : 25 %
- Russell 2000 : 15 %
- Nikkei : 15 %
- VIX inverse : 20 %

Overlay futures PREOPEN : 20 % dans la limite définie par le moteur.

## 7. PIT V24.4.1

### 7.1 Epoch

`V24.4.1_ONLY_NO_MIX_WITH_V24.4.0`

Le ledger V24.4.0 reste historique. Il ne doit jamais être concaténé aux métriques V24.4.1.

### 7.2 Ledger

- Predictions : `state/tct_context/TCT_V24_4_1_CATALYST_LEDGER.csv`
- Clôtures daily : `state/tct_context/TCT_DAILY_CLOSE_LEDGER.csv`

### 7.3 Causalité

- Premier snapshot d'un couple jour/phase/ISIN conservé.
- News connues avant snapshot seulement.
- Outcome : première clôture daily réellement observée strictement après la date de référence.
- Relecture de 10 dernières barres du cache local pour récupérer J+1 si un workflow a été manqué.
- Minimum 80 % de couverture outcome du snapshot avant exposition au validateur.
- Aucun backfill de snapshots TCT historiques reconstruit avec information actuelle.

### 7.4 Fingerprint

Algorithme : `TCT_PIT_SHA256_CANONICAL_V2`.

Le hash porte sur une liste fixe de champs décisionnels. Une nouvelle colonne auxiliaire ne doit pas invalider un ancien snapshot. Toute mutation d'un champ décisionnel déjà figé déclenche un fail-closed.

## 8. Gates de validation V24.4.1

### 8.1 Maturité minimum

- 60 lignes PREOPEN étiquetées ;
- 20 ISIN distincts ;
- 15 alertes `movement_potential_score >=70` ;
- 20 appels directionnels `|direction_bias_score| >=25` ;
- 15 séances distinctes.

### 8.2 Comparateur

V24.3.1 technique seul :
- amplitude : `technical_impulse_score` ;
- direction : `technical_direction_score`.

### 8.3 Critères principaux

Pour considérer les critères de recherche atteints, au moins 2 sur 3 :

1. amélioration Recall Top10 >= +10 points vs technique seule ;
2. amélioration lift décile supérieur >= +0,15 ;
3. amélioration Spearman score/amplitude >= +0,10.

En complément : faux fort potentiel <=60 %.

### 8.4 Direction

- Hit rate directionnel >=55 % ;
- aucune dégradation vs technique seule.

Un PASS ne vaut jamais promotion automatique.

## 9. Sorties

### Daily/Weekly

- `outputs/daily_tct_ct/TCT_DAILY_TRADER_V24_3_1_SHADOW.csv`
- `outputs/mobile/ANDROID_TCT_DAILY_TRADER_SHADOW.md`
- `outputs/audit/TCT_DAILY_TRADER_V24_3_1_AUDIT.json`
- `state/tct_context/TCT_DAILY_TRADER_LATEST.csv`

### V24.4.1

- `outputs/daily_tct_ct/TCT_NEXT_SESSION_CATALYST_V24_4_1.csv`
- `outputs/daily_tct_ct/TCT_V24_4_1_PIT_SLICES.csv`
- `outputs/daily_tct_ct/TCT_V24_4_1_PREOPEN_POSTMARKET_CHANGES.csv`
- `outputs/mobile/ANDROID_TCT_NEXT_SESSION_CATALYST.md`
- `outputs/mobile/ANDROID_TCT_V24_4_1_PIT_VALIDATION.md`
- `outputs/audit/TCT_NEXT_SESSION_CATALYST_V24_4_1_AUDIT.json`
- `outputs/audit/TCT_V24_4_1_PIT_LINEAGE_AUDIT.json`
- `outputs/audit/TCT_V24_4_1_PIT_VALIDATION.json`

## 10. Workflows

- Daily collecte/TCT : `.github/workflows/committee_tct_ct_daily.yml`
- PREOPEN/POSTMARKET : `.github/workflows/tct_next_session_context.yml`

Le workflow daily met à jour V24.3.1 et le close ledger. Le workflow catalysts exécute : snapshot V24.4.1 → lineage fail-closed → validation PIT.

## 11. Gouvernance finale

V24.4.1 est `SHADOW_RESEARCH_ONLY`. Aucun poids ni état SHADOW ne doit être converti en décision réelle avant maturité, audit PIT/OOS et décision explicite. CT et ETF restent hors du périmètre de cette couche. Le référentiel de production V21.8.1 reste inchangé.
