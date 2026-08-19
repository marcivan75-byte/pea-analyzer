# TCT V24.4.0 — Protocole de validation PIT

Date : 19/08/2026

## Objet

Valider empiriquement si la couche V24.4.0 améliore l'identification, avant l'ouverture européenne, des actions TCT susceptibles d'enregistrer les plus fortes amplitudes de la séance suivante.

La validation est séparée de la production. V24.4 reste SHADOW tant qu'une décision explicite de promotion n'a pas été prise après maturité et revue PIT/OOS.

## 1. Source de vérité PIT

Le fichier unique de validation est :

`state/tct_context/TCT_V24_4_0_CATALYST_LEDGER.csv`

Pour chaque titre/jour/phase, le premier snapshot est conservé. Un rerun ne remplace pas l'observation initiale.

La population cible du test est uniquement `PREOPEN`.

Une ligne n'entre dans l'échantillon évalué que lorsque `realized_abs_return_pct` a été ajouté à partir d'une clôture daily ultérieure.

Les lignes POSTMARKET sont conservées pour analyser l'évolution du signal entre POSTMARKET et PREOPEN, mais elles ne comptent pas dans la maturité principale.

## 2. Anti-look-ahead

- news : timestamp exploitable obligatoire ;
- fenêtre PREOPEN : de la dernière vraie clôture daily au snapshot PREOPEN ;
- résultat réel : ajouté uniquement après une clôture ultérieure ;
- score antérieur jamais recalculé avec le résultat futur ;
- premier snapshot PIT conservé ;
- holdout final fermé.

## 3. Maturité minimale

Avant toute conclusion statistique :

- au moins 60 lignes PREOPEN étiquetées ;
- au moins 20 ISIN distincts ;
- au moins 15 alertes `movement_potential_score >= 70` ;
- au moins 20 appels directionnels avec `|direction_bias_score| >= 25` ;
- au moins 15 séances distinctes.

Si une seule condition manque, le verdict obligatoire est :

`NOT_MATURE_ACCUMULATING_PIT` / `NOT_EVALUABLE_BEFORE_MATURITY`.

## 4. Comparateur

Le gain V24.4 est comparé à une lecture technique issue uniquement du seed V24.3.1 :

- amplitude baseline : `technical_impulse_score` ;
- direction baseline : `technical_direction_score`.

Cela évite de conclure que V24.4 est utile simplement parce que le marché était prévisible avec la technique daily/weekly seule.

## 5. Métriques principales

### Recall Top 10

Pour chaque snapshot, comparer :

- Top 10 prévu par `movement_potential_score` ;
- Top 10 réellement observé par amplitude absolue.

Le recall agrégé mesure la part des vrais Top 10 retrouvés.

### Lift du décile supérieur

Rapport :

`moyenne amplitude absolue du décile supérieur prévu / moyenne amplitude absolue de l'échantillon`.

### Corrélation de rang

Spearman entre `movement_potential_score` et amplitude absolue réalisée.

### Faux fort potentiel

Une alerte `score >= 70` est considérée comme faux fort potentiel si l'amplitude réelle termine hors du quartile supérieur de son snapshot PREOPEN.

## 6. Critères de recherche pré-enregistrés

Avant les premiers résultats réels V24.4, les seuils suivants sont gelés :

- amélioration Recall Top 10 vs technique seule : >= +10 points ;
- amélioration du lift décile supérieur : >= +0,15 ;
- amélioration Spearman : >= +0,10 ;
- au moins 2 de ces 3 améliorations doivent être satisfaites ;
- taux de faux fort potentiel <= 60 %.

La direction est secondaire et séparée :

- hit rate directionnel >= 55 % ;
- pas de dégradation vs `technical_direction_score`.

Un PASS directionnel n'est pas nécessaire pour conclure sur la capacité de classement d'amplitude, mais il est requis avant toute utilisation directionnelle de V24.4.

## 7. Slices obligatoires

Le rapport publie les résultats par :

- type de news ;
- secteur ;
- état d'entrée V24.3.1 ;
- régime global risk-on/risk-off ;
- proximité des résultats ;
- changement POSTMARKET → PREOPEN.

## 8. États du validateur

### Avant maturité

`NOT_MATURE_ACCUMULATING_PIT`

`NOT_EVALUABLE_BEFORE_MATURITY`

### Après maturité

`RESEARCH_CRITERIA_MET` ou `RESEARCH_CRITERIA_NOT_MET`.

Même `RESEARCH_CRITERIA_MET` ne possède aucune autorité de production : il permet seulement une revue manuelle et une éventuelle hypothèse gelée pour l'étape suivante.

## 9. Sorties

À chaque snapshot V24.4 :

- `outputs/audit/TCT_V24_4_0_PIT_VALIDATION.json` ;
- `outputs/daily_tct_ct/TCT_V24_4_0_PIT_SLICES.csv` ;
- `outputs/daily_tct_ct/TCT_V24_4_0_PREOPEN_POSTMARKET_CHANGES.csv` ;
- `outputs/mobile/ANDROID_TCT_V24_4_PIT_VALIDATION.md`.

## 10. Gouvernance

- `production_influence = 0.0` ;
- `promotion_authority = false` ;
- `retuning_allowed = false` ;
- holdout fermé ;
- CT inchangé ;
- aucun ordre réel.

La validation PIT sert à décider si V24.4 mérite de passer à l'étape de validation suivante ; elle ne constitue jamais une promotion automatique.
