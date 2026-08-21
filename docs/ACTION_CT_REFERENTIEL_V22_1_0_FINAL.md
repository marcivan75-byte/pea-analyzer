# Référentiel final Actions CT V22.1.0 — Context Enriched SHADOW

Date : 21/08/2026  
Univers : Actions PEA  
Horizon CT : 10 à 60 séances, environ 2 à 12 semaines  
Statut : `SHADOW_RESEARCH_ONLY`

## 1. Architecture

Le CT est désormais organisé en quatre niveaux clairement séparés :

1. **baseline de sélection** : `V21_ACTIONS_REFERENCE_V21_0.json` ;
2. **moteur technique CT** : V22.0 daily/weekly ;
3. **moteur enrichi CT** : V22.1 contexte sectoriel, relatif, qualité, thème/macro ;
4. **gouvernance Entry/Exit** : V21.8.1, inchangée pour la production.

V22.1 ne modifie ni la baseline, ni les ordres, ni le sizing. Il enrichit le processus et accumule une preuve PIT permettant une future décision de promotion.

## 2. Périmètre d'amélioration intégré

### 2.1 Technique daily

- SMA20, SMA50, SMA120 ;
- pente SMA20 et SMA50 ;
- performance 10, 20, 60 séances ;
- RSI14 avec pénalisation de sur-extension ;
- accélération du momentum ;
- ATR14 ;
- gap exprimé en ATR ;
- position de clôture dans le range ;
- breakout 55 et 120 séances ;
- mémoire des cassures ;
- retest 10 séances ;
- failed breakout ;
- plus bas 20 séances ;
- invalidation structurelle.

### 2.2 Weekly

Le weekly est reconstruit depuis le daily et n'utilise que des semaines terminées pour ses statistiques :

- moyenne 10 semaines ;
- moyenne 20 semaines ;
- momentum 4 semaines ;
- momentum 8 semaines ;
- qualité de clôture hebdomadaire.

Une semaine incomplète n'est jamais assimilée à une semaine terminée.

### 2.3 Volume et liquidité

- RVOL vs médiane 20 séances ;
- accélération du volume 5 jours vs moyenne 20 jours ;
- turnover médian 20 jours ;
- distribution sur trois séances ;
- upper wick avec volume ;
- seuil de turnover de recherche : 500 000 EUR.

### 2.4 Rotation sectorielle

V22.1 reconstruit à chaque run le contexte déjà gouverné par `sector_rotation.py` :

- distance médiane au plus haut 52 semaines ;
- momentum 1 mois ;
- momentum 3 mois ;
- accélération du secteur ;
- breadth au-dessus MM50 ;
- breadth au-dessus MM200 ;
- inflexion relative ;
- score de rotation sectorielle ;
- catch-up secteur ;
- catch-up action ;
- régime de marché lié à la proximité des plus hauts.

Une forte distance au plus haut n'est jamais récompensée seule : le recovery gate existant du module de rotation est conservé.

### 2.5 Force relative cross-sectionnelle

Le CT calcule une force relative actuelle lorsqu'un minimum de 20 Actions est disponible :

- rang performance 1 mois : 50 % ;
- rang performance 3 mois : 30 % ;
- rang performance 6 mois : 20 %.

Les horizons absents sont simplement exclus et les poids disponibles sont renormalisés. Une valeur déjà observée dans le master prévaut sur ce fallback dérivé.

### 2.6 Qualité / target

Le CT réutilise les features Actions gouvernées existantes :

- `morningstar_action_score` ;
- `target_upside_growth_score` ;
- `target_upside_gt4_score`.

Les dividendes restent disponibles dans le référentiel Actions mais ne deviennent pas un moteur important de timing CT.

### 2.7 Consensus et catalyseurs

Lorsque disponibles :

- consensus ;
- potentiel cible ;
- évolution du consensus 4 semaines ;
- upgrades nets 30 jours ;
- news catalyst ;
- earnings catalyst.

Aucune donnée absente n'est remplacée par une valeur neutre.

### 2.8 Thèmes

V22.1 peut exploiter les sorties gouvernées du module thématique :

- `theme_rotation_exposure_score` ;
- `theme_risk_adjusted_score` ;
- `theme_confluence_score` ;
- `theme_weighted_AVCR`.

Le CT peut donc identifier un titre exposé à plusieurs thèmes forts tout en détectant la survalorisation thématique.

### 2.9 Macro

Le score `sector_macro_score` est accepté uniquement si `macro_evidence_sufficient=true`. Dans le cas contraire, le facteur macro est considéré comme absent et non comme neutre.

## 3. Pondération entrée V22.1

| Bloc | Poids |
|---|---:|
| Trend structure | 20 % |
| Momentum quality | 18 % |
| Weekly alignment | 16 % |
| Relative strength / secteur | 14 % |
| Volume / liquidité | 10 % |
| Catalyseurs / consensus | 10 % |
| Quality / target | 6 % |
| Theme / macro | 6 % |
| **Total** | **100 %** |

Les blocs absents réduisent la couverture. Le score n'impute jamais 50 à une donnée absente.

## 4. Confirmations d'entrée

Huit familles sont possibles :

- TREND ;
- MOMENTUM ;
- WEEKLY ;
- VOLUME ;
- SECTOR ;
- CATALYST ;
- QUALITY ;
- THEME_MACRO.

### Seuils

- `ENTRY_READY_SHADOW` : score >= 70, couverture >= 80 %, au moins 4 confirmations et TREND obligatoire ;
- `ENTRY_STRONG_SHADOW` : score >= 80, au moins 5 confirmations, TREND et WEEKLY obligatoires.

Les gates de risque restent prioritaires sur le score.

## 5. Gates de risque d'entrée

Les états suivants peuvent bloquer une entrée même avec un score élevé :

- `LIQUIDITY_WARNING_SHADOW` ;
- `ENTRY_CONFLICT_SHADOW` ;
- `WEEKLY_CONFLICT_SHADOW` ;
- `WAIT_RISK_SHADOW` ;
- `WAIT_PULLBACK_SHADOW` ;
- `WAIT_CONTEXT_RISK_SHADOW`.

### Paramètres principaux

- weekly adverse < 35 ;
- risque sortie incompatible >= 50 ;
- RVOL confirmation >= 1,15 ;
- accélération volume >= 1,10 ;
- gap excessif >= 1,50 ATR ;
- sur-extension >= 2 ATR au-dessus SMA20 ;
- invalidation structurelle supérieure à 7 % : attente ;
- earnings risk : J-2 à J0 ;
- thème survalorisé : AVCR >= 65 ;
- macro adverse : score < 35 lorsqu'il est suffisamment documenté.

## 6. Secteur ou thème prometteur mais survalorisé

Deux protections coexistent :

1. `SECTOR_HOT_VALUATION_RISK` : secteur fort avec faible discount de valorisation ;
2. `THEME_OVERVALUATION_RISK` : AVCR thématique élevé.

Lorsque la survalorisation thématique se combine à un contexte macro adverse, V22.1 produit `WAIT_CONTEXT_RISK_SHADOW` plutôt que de poursuivre le mouvement mécaniquement.

## 7. Risque de sortie V22.1

| Bloc | Poids |
|---|---:|
| Trend break | 26 % |
| Momentum deterioration | 18 % |
| Weekly deterioration | 18 % |
| Distribution / volume | 10 % |
| Relative strength deterioration | 10 % |
| Volatility risk | 10 % |
| Valuation / event risk | 8 % |
| **Total** | **100 %** |

Le risque valorisation/événement combine uniquement les informations disponibles :

- inverse du valuation discount ;
- AVCR thématique ;
- risque résultats J-2/J0.

## 8. Confirmation temporelle de sortie

Un `EXIT_RISK_HIGH_CANDIDATE_SHADOW` ne devient `EXIT_RISK_HIGH_SHADOW` qu'après une alerte provenant d'une séance de marché antérieure.

Un rerun le même jour ne peut jamais confirmer artificiellement une sortie.

## 9. Stop et prise de bénéfice

V22.1 ne crée aucune règle arbitraire de vente à +4 % ou de stop à -18 %.

- take-profit fixe : désactivé ;
- stop-loss fixe : non promu ;
- invalidation structurelle : recherche uniquement ;
- plafond de distance d'invalidation de 7 % : filtre d'entrée, pas garantie d'exécution.

## 10. PIT

Epoch : `ACTION_CT_V22.1.0_ONLY`.

Le premier snapshot `date + ISIN` est immuable et fingerprinté en SHA-256 sur les champs décisionnels V22.1, incluant les blocs qualité/thème/macro.

Outcomes :

- 10 séances ;
- 20 séances, horizon principal ;
- 40 séances ;
- MFE 20 séances ;
- MAE 20 séances.

L'entrée de mesure est l'open de la première séance strictement postérieure au snapshot.

## 11. Gates de validation

Maturité minimale :

- 300 observations étiquetées à 20 séances ;
- 50 ISIN distincts ;
- 20 dates de snapshot ;
- 40 ENTRY_READY/STRONG.

Comparateur principal : baseline CT V21.0.

Gates :

- win rate : +5 points minimum vs baseline BUY ;
- rendement médian 20 séances : +0,5 point minimum ;
- Spearman score/rendement >= 0,10 ;
- faux positifs <= 45 % ;
- dégradation MAE médiane <= 0,5 point.

V22.0 reste disponible comme comparateur secondaire pour mesurer la valeur ajoutée spécifique de la couche contexte V22.1.

Même un PASS ne déclenche aucune promotion automatique.

## 12. Exécution quotidienne

Le workflow quotidien exécute :

1. collecte/enrichissement ;
2. baseline TCT/CT V21.8.1 ;
3. Actions CT V22.0 parent ;
4. Actions CT V22.1 enrichi ;
5. TCT V24.3.1/V24.4.1 ;
6. audits et restitutions mobiles ;
7. persistance des états PIT.

## 13. Package complet

Package : `ACTION_CT_V22_1_0_COMPLETE`.

Le manifest exhaustif est situé dans :

`packages/ACTION_CT_V22_1_0/MANIFEST.json`

Construction :

`python scripts/build_action_ct_package_v22_1.py --output dist/ACTION_CT_V22_1_0_COMPLETE.zip`

La CI publie également ce ZIP comme artefact GitHub.

## 14. Statut final

V22.1 représente la version CT la plus complète du process au 21/08/2026, mais demeure en SHADOW jusqu'à accumulation de preuve forward-PIT suffisante. Cette restriction concerne uniquement la promotion statistique ; le moteur, ses audits, ses sorties et son historique PIT sont intégrables immédiatement dans le workflow quotidien.
