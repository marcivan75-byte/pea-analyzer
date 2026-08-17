# RISK V1.1 — FINAL GUARDRAILS

## Statut de production

RISK V1.1 reste un moteur **CONTEXT_ONLY**. Il peut publier des diagnostics de bêta, downside bêta, corrélation, stress, concentration et diversification, mais il ne peut modifier ni score, ni décision, ni sizing, ni stop-loss, ni ordre.

Les trois hypothèses de sizing bêta déjà testées en OOS restent rejetées. Aucun retuning post-résultat n'est autorisé.

## Benchmark

Le benchmark commun est le proxy PEA Actions robuste V2 : moyenne équipondérée quotidienne après winsorisation transversale 5/95, contrôle de largeur quotidienne et fail-closed si le benchmark lui-même devient incohérent.

## Gate de fiabilité bêta

Le run représentatif #32048427668 a montré que certaines séries individuelles très faiblement corrélées au benchmark pouvaient produire des bêtas numériquement extrêmes tout en ayant un R² très faible. Ces valeurs sont mathématiquement calculables, mais elles ne doivent pas alimenter un verdict de risque, un bêta portefeuille ou un scénario de stress systématique.

La règle devient donc explicite :

- R² >= 0,60 : `HIGH` ;
- 0,35 <= R² < 0,60 : `MEDIUM` ;
- 0,15 <= R² < 0,35 : `LOW` ;
- R² < 0,15 : `VERY_LOW`.

Quand la fiabilité est `VERY_LOW`, le moteur conserve corrélation et R² pour audit, mais neutralise en fail-closed les champs dérivés de bêta : bêta 63/126/252 jours, upside/downside bêta, ratio downside/upside, stabilité et classe de bêta. Le statut devient `UNRELIABLE_LOW_R2`.

Cette correction ne change aucune formule pour les observations de fiabilité `LOW`, `MEDIUM` ou `HIGH` et ne change aucune décision Comité.

## Gouvernance

- `decision_influence = 0.0`
- `score_influence = 0.0`
- `sizing_execution_influence = 0.0`
- `stop_loss_influence = 0.0`
- `real_orders_enabled = false`
- holdout final inchangé et non ouvert
- exact holdings overlap non promouvable tant que les snapshots PIT suffisants ne sont pas disponibles

Toute future promotion d'une règle Risk exige une hypothèse indépendante pré-enregistrée et un gain marginal PIT/OOS sur espérance, drawdown maximal, queue de pertes, taux de réussite et rendement ajusté du risque.