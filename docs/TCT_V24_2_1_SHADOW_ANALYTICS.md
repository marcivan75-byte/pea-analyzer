# TCT V24.2.1 — Analytics SHADOW

Date : 18/08/2026

## Objet

V24.2.1 analyse automatiquement les observations produites par la couche TCT V24.2.0 Intraday/Scalping SHADOW. Elle ne modifie aucun signal, aucune pondération, aucun sizing, aucun stop et ne possède aucune autorité de promotion.

## Dimensions suivies

Les métriques sont publiées pour :

- ensemble des observations ;
- setup intraday ;
- source T1/T2 ;
- rang de session après le signal (`J+1`, `J+2`, `J+3`) ;
- tranche horaire de l'entrée SHADOW ;
- tranche de score ;
- état SHADOW.

## Métriques

Pour les événements d'entrée causaux uniquement :

- nombre d'entrées ;
- taux d'entrée ;
- nombre d'ISIN distincts ;
- expectancy brute jusqu'à la clôture ;
- médiane ;
- taux de trades positifs ;
- gain moyen ;
- perte moyenne ;
- profit factor brut ;
- MFE moyen ;
- MAE moyen ;
- meilleur et pire rendement à la clôture.

L'expectancy nette n'est volontairement pas calculée tant que la couverture de friction réelle (spread/slippage/frais) est insuffisante.

## Maturité de l'échantillon

Les seuils sont pré-enregistrés avant accumulation des résultats :

- moins de 10 entrées : `ACCUMULATING_EARLY` ;
- 10 à 29 entrées : `ACCUMULATING_DESCRIPTIVE_ONLY` ;
- à partir de 30 entrées : contrôle supplémentaire de diversité ;
- au moins 10 ISIN distincts pour une revue candidate ;
- au moins 15 entrées dans un setup pour considérer son sous-échantillon comme suffisamment alimenté.

Même lorsque ces seuils sont atteints, le statut maximal est `READY_FOR_PRE_REGISTERED_REVIEW_NOT_PROMOTION`.

## Gouvernance

- `promotion_authority = false`
- `retuning_allowed = false`
- décision/score/sizing/stop influence = 0
- holdout final fermé
- aucun ordre réel

Aucune repondération ou modification de seuil n'est autorisée sur la base des premiers résultats. Une hypothèse candidate devra être gelée puis validée séparément en PIT/OOS.

## Sorties quotidiennes

- `outputs/daily_tct_ct/TCT_INTRADAY_V24_2_1_ANALYTICS.csv`
- `outputs/daily_tct_ct/TCT_INTRADAY_V24_2_1_ENRICHED_OBSERVATIONS.csv`
- `outputs/audit/TCT_INTRADAY_V24_2_1_ANALYTICS.json`
- `outputs/mobile/ANDROID_TCT_INTRADAY_ANALYTICS.md`

Le workflow quotidien exécute l'analyse après la collecte intraday SHADOW avec `continue-on-error: true`, afin qu'une panne analytique ne puisse jamais bloquer le TCT/CT canonique.
