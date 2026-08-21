# Référentiel Actions CT V22.0.0 — SHADOW

Date : 21/08/2026  
Périmètre : Actions PEA — Court Terme (CT)  
Horizon fonctionnel : environ 2 à 12 semaines, soit 10 à 60 séances  
Nature : challenger de recherche et aide à la décision, aucun ordre réel

## 1. Hiérarchie normative

1. **Baseline CT de référence** : `V21_ACTIONS_REFERENCE_V21_0.json`, inchangée.
2. **Référentiel Actions enrichi** : `V21_ACTIONS_CRITERIA_REGISTRY.json`, conservé comme challenger non certifié PIT/OOS.
3. **Entry/Exit canonique** : V21.8.1, inchangé.
4. **Challenger Actions CT** : V22.0.0 SHADOW.
5. **TCT** : V24.4.1 SHADOW, strictement séparé du CT.

La présence de la baseline V21.0 dans le runner quotidien est volontaire : V21.4 ne doit pas remplacer cette référence avant preuve PIT/OOS. Le dernier audit V21.8.1 avait mesuré environ 52,98 % de couverture pondérée sur le challenger Actions CT, insuffisant pour promouvoir une nouvelle pondération.

## 2. Principes non négociables

- T1/T2 interdits dans le CT ; ils restent réservés à ACTION TCT.
- Aucun transfert automatique des formules TCT vers CT.
- Aucun intraday, 1m ou 5m.
- OHLCV daily comme donnée de marché principale.
- Weekly calculé localement depuis le daily.
- La semaine courante incomplète n'est pas assimilée à une semaine terminée.
- La bougie daily du jour est exclue avant la garde locale de 18:00 Europe/Paris.
- Aucun take-profit fixe opérationnel.
- Aucun stop-loss fixe arbitraire promu.
- L'invalidation structurelle à 7 % est un plafond de risque de recherche pour différer une entrée, pas un ordre automatique.
- Aucun score V22.0 ne modifie la baseline V21.0.
- Influence décision, score, sizing et stop : 0.
- Holdout final fermé.
- Aucun ordre réel.

## 3. Objectif du nouveau CT

V22.0 vise à corriger les faiblesses du CT historique sans retuner prématurément les poids du référentiel général. Le moteur sépare désormais :

- **sélection de référence** : baseline V21.0 ;
- **qualité du timing CT** : confluence daily/weekly ;
- **contexte sectoriel et relatif** ;
- **catalyseurs/consensus** ;
- **risque d'entrée** ;
- **risque de sortie** ;
- **validation forward-PIT**.

La philosophie reprise du TCT finalisé est méthodologique : causalité, données terminées, confluence, fail-closed, état temporel et validation PIT. Les formules T1/T2 et les fenêtres TCT ne sont pas reprises.

## 4. Données et lookbacks

### 4.1 Données

- OHLCV daily existant dans `data/cache/actions` ;
- minimum 140 barres quotidiennes ;
- weekly dérivé du daily ;
- données enrichies du master Actions uniquement lorsqu'elles sont réellement disponibles ;
- aucune imputation neutre des blocs absents ;
- couverture pondérée calculée sur les blocs réellement observés.

### 4.2 Fenêtres CT

| Élément | Fenêtre |
|---|---:|
| ATR | 14 séances |
| Volume / liquidité | 20 séances |
| Momentum rapide | 10 séances |
| Momentum intermédiaire | 20 séances |
| Momentum lent | 60 séances |
| SMA rapide | 20 séances |
| SMA intermédiaire | 50 séances |
| SMA lente | 120 séances |
| Breakout rapide | 55 séances |
| Breakout lent | 120 séances |
| Mémoire retest | 10 séances |
| Weekly tendance rapide | 10 semaines |
| Weekly tendance lente | 20 semaines |
| Weekly momentum rapide | 4 semaines |
| Weekly momentum lent | 8 semaines |

## 5. Score d'entrée CT V22.0

| Bloc | Poids |
|---|---:|
| Structure de tendance | 25 % |
| Qualité du momentum | 20 % |
| Alignement weekly | 20 % |
| Force relative / secteur | 15 % |
| Volume / liquidité | 10 % |
| Catalyseurs / consensus | 10 % |
| **Total** | **100 %** |

### 5.1 Structure de tendance

Le bloc exploite notamment :

- cours vs SMA20 / SMA50 / SMA120 ;
- ordre SMA20 >= SMA50 >= SMA120 ;
- pente SMA20 et SMA50 ;
- breakout 55 séances ;
- breakout 120 séances ;
- retest valide sur mémoire 10 séances ;
- failed breakout.

Un breakout récent est mémorisé au lieu de disparaître du diagnostic dès la séance suivante.

### 5.2 Momentum

Le moteur combine :

- performance 10 séances ;
- performance 20 séances ;
- performance 60 séances ;
- RSI14 avec pénalisation de la sur-extension ;
- accélération du momentum 10 jours courant vs 10 jours précédents.

### 5.3 Weekly

Le weekly confirme le CT par :

- cours weekly vs moyenne 10 semaines ;
- moyenne 10 semaines vs moyenne 20 semaines ;
- momentum 4 semaines ;
- momentum 8 semaines ;
- qualité de clôture weekly.

Une semaine en cours n'est pas utilisée comme semaine terminée pour les moyennes et rendements hebdomadaires.

### 5.4 Secteur et force relative

Le bloc peut utiliser, lorsqu'ils sont disponibles :

- `sector_rotation_score` ;
- `action_catchup_score` ;
- `relative_strength` ;
- `market_high_regime_score` en contexte secondaire.

Une rotation sectorielle forte ne suffit jamais à créer seule une entrée.

### 5.5 Catalyseurs et consensus

Le bloc peut exploiter :

- consensus ;
- potentiel cible ;
- évolution du consensus sur 4 semaines ;
- upgrades nets 30 jours ;
- news catalyst ;
- earnings catalyst.

Les données absentes réduisent la couverture ; elles ne sont pas remplacées par un score neutre inventé.

## 6. Gates d'entrée

| Paramètre | Valeur SHADOW |
|---|---:|
| ENTRY_READY | 70 |
| ENTRY_STRONG | 80 |
| Couverture pondérée minimum | 80 % |
| Confirmations minimum READY | 3 |
| Confirmations minimum STRONG | 4 |
| Weekly favorable | >= 60 |
| Weekly adverse | < 35 |
| Risque sortie incompatible avec entrée | >= 50 |
| RVOL confirmation | >= 1,15 |
| Accélération volume | >= 1,10 |
| Gap excessif | >= 1,50 ATR en valeur absolue |
| Sur-extension vs SMA20 | >= 2,00 ATR |
| Plafond risque invalidation structurelle | 7 % |
| Turnover médian 20j minimum | 500 000 EUR |

Confirmations possibles : TREND, MOMENTUM, WEEKLY, VOLUME, SECTOR, CATALYST.

La confirmation **TREND est obligatoire** pour `ENTRY_READY_SHADOW` et `ENTRY_STRONG_SHADOW`.

### États d'entrée

- `DATA_INSUFFICIENT`
- `LIQUIDITY_WARNING_SHADOW`
- `ENTRY_CONFLICT_SHADOW`
- `WEEKLY_CONFLICT_SHADOW`
- `WAIT_RISK_SHADOW`
- `WAIT_PULLBACK_SHADOW`
- `ENTRY_STRONG_SHADOW`
- `ENTRY_READY_SHADOW`
- `WAIT_SHADOW`

## 7. Warning secteur prometteur mais survalorisé

V22.0 matérialise explicitement le risque demandé pour les secteurs en forte rotation :

- contexte sectoriel >= 70 ;
- `valuation_discount_score <= 30` ;
- émission de `SECTOR_HOT_VALUATION_RISK`.

Ce warning ne transforme pas mécaniquement le titre en vente ; il interdit de considérer la seule force sectorielle comme justification suffisante d'entrée.

Un warning spécifique est également publié si les résultats sont à moins de deux jours : `EARNINGS_EVENT_WITHIN_2D`.

## 8. Risque de sortie

| Bloc | Poids |
|---|---:|
| Rupture de tendance | 30 % |
| Détérioration momentum | 20 % |
| Détérioration weekly | 20 % |
| Distribution / volume | 10 % |
| Détérioration relative | 10 % |
| Risque volatilité | 10 % |
| **Total** | **100 %** |

### États de sortie

- `DATA_INSUFFICIENT`
- `EXIT_WATCH_SHADOW`
- `EXIT_RISK_HIGH_SHADOW`
- `HOLD_SUPPORTIVE_SHADOW`

Un risque élevé brut ne devient `EXIT_RISK_HIGH_SHADOW` que s'il est confirmé par un warning d'une **séance de marché antérieure**. Un rerun le même jour ne peut pas créer artificiellement cette confirmation.

La structure peut confirmer le risque via :

- failed breakout ;
- clôture sous le plus bas précédent ;
- deux distributions sur trois séances ;
- rupture SMA50.

## 9. Invalidation et pertes

Le moteur calcule un niveau d'invalidation structurelle à partir des supports observés : breakout, SMA20, SMA50 et plus bas 20 séances.

La règle de 7 % est uniquement utilisée comme **plafond de distance acceptable à l'entrée dans le challenger**. Elle ne garantit pas une perte maximale et n'est pas un stop aveugle : gaps et slippage peuvent dépasser tout niveau théorique.

Aucune règle de prise de bénéfice fixe n'est introduite.

## 10. PIT V22.0

### 10.1 Epoch

`ACTION_CT_V22.0.0_ONLY`

Les snapshots d'autres versions ne doivent pas être mélangés aux métriques V22.0.

### 10.2 Ledger

`state/action_ct/ACTION_CT_V22_0_0_PIT_LEDGER.csv`

Le premier snapshot d'un couple `date / ISIN` est immuable. Une tentative de mutation d'un champ décisionnel déjà figé déclenche `FAIL_CLOSED_FINGERPRINT_MISMATCH` et le premier snapshot reste conservé.

### 10.3 Mesure des résultats

- entrée de mesure : **open de la première séance strictement postérieure au snapshot** ;
- rendement : clôture après 10, 20 et 40 séances ;
- horizon principal : 20 séances ;
- MFE 20 séances ;
- MAE 20 séances.

Aucun snapshot historique CT ne peut être reconstruit avec les données d'aujourd'hui.

## 11. Maturité et validation

Avant toute interprétation :

- 300 lignes PIT étiquetées à 20 séances ;
- 50 ISIN distincts ;
- 20 dates de snapshot distinctes ;
- 40 `ENTRY_READY/STRONG` étiquetés.

Comparateur : baseline `V21_ACTIONS_REFERENCE_V21_0_CT` prise au même snapshot.

Tous les gates suivants doivent être franchis pour un `PASS_RESEARCH_GATES_NO_AUTO_PROMOTION` :

1. win rate des ENTRY_READY/STRONG >= baseline BUY + 5 points ;
2. médiane rendement 20 séances >= baseline BUY + 0,5 point ;
3. Spearman score entrée / rendement 20 séances >= 0,10 ;
4. faux positifs <= 45 % ;
5. dégradation de MAE médiane <= 0,5 point.

Même un PASS n'autorise aucune promotion automatique.

## 12. Sorties

- `outputs/daily_tct_ct/ACTION_CT_V22_0_0_SHADOW.csv`
- `outputs/mobile/ANDROID_ACTION_CT_V22_SHADOW.md`
- `outputs/mobile/ANDROID_ACTION_CT_V22_PIT_VALIDATION.md`
- `outputs/audit/ACTION_CT_V22_0_0_AUDIT.json`
- `outputs/audit/ACTION_CT_V22_0_0_PIT_VALIDATION.json`
- `state/action_ct/ACTION_CT_V22_0_0_LATEST.csv`
- `state/action_ct/ACTION_CT_V22_0_0_PIT_LEDGER.csv`
- `state/action_ct/ACTION_CT_V22_0_0_EXIT_STATE.csv`

## 13. Gouvernance finale

V22.0.0 est `SHADOW_RESEARCH_ONLY`. La baseline CT V21.0 et V21.8.1 restent inchangées. V22.0.0 doit accumuler des observations forward-PIT avant toute décision sur les poids, seuils ou promotion.
