# ACTION MT V1.0.0 — référentiel shadow

Le module classe les Actions PEA sur un horizon de trois à douze mois. Il ne remplace pas ACTION CT : il privilégie la persistance de tendance, la qualité économique, la croissance, la valorisation, les révisions, le contexte sectoriel et la maîtrise du risque.

## Principes de décision

- Socle de marché : au moins 200 séances, tendances 50/100/200 jours, performances 3/6/12 mois, RSI, volatilité, risque baissier, drawdown et liquidité en euros.
- Qualité du parcours : efficacité directionnelle à 63 séances, Sharpe/Sortino 126 séances, gain-to-pain, volume relatif et tendance des volumes, inspirés du moteur ETF MT.
- Socle entreprise : qualité, rentabilité, bilan, croissance du résultat/chiffre d'affaires/free cash-flow, valorisation et révisions des analystes.
- Contexte : rotation sectorielle, macro uniquement si la preuve est suffisante, et régime de marché.
- Les valeurs absentes ne reçoivent jamais un score neutre. Les poids sont renormalisés, mais une couverture minimale de 72 % et les blocs tendance/risque/liquidité restent obligatoires.
- Une liquidité inférieure à 500 kEUR/jour, un drawdown d'au moins 45 % ou un régime de marché fortement défavorable bloque l'entrée.
- Une entrée exige quatre confirmations et une entrée forte cinq confirmations parmi tendance, momentum, qualité, rendement ajusté du risque, secteur et volume.
- Le comité final combine 55 % de score absolu et 45 % de rang transversal, limite les nouvelles positions à trois et plafonne à deux les expositions d'un même secteur.
- Hors régime favorable, le comité s'abstient explicitement. Une survalorisation dans un secteur déjà très chaud produit un conflit de contexte et bloque la sélection.

## Gouvernance

La version est exclusivement `SHADOW`. Aucun ordre réel n'est autorisé. Les données structurelles du snapshot courant ne peuvent pas être utilisées pour reconstruire un backtest historique. Toute promotion en production exige des données point-in-time, coûts et slippage, walk-forward, holdout frais et validation de la concentration sectorielle.

La sortie est évaluée à la clôture : stop dur -18 %, trailing stop -12 % après 40 séances et revue temporelle à 252 séances. Aucun objectif de gain fixe n'est imposé afin de laisser courir les tendances MT.

## Contrôle décisionnel du CI

Le CI ne se limite pas aux tests unitaires. Il valide les sommes de poids, les seuils de couverture, les verrous de gouvernance, une sélection en régime favorable et une abstention en régime adverse. Il publie `ACTION_MT_CI_DECISION.json` comme artefact auditable et échoue si un invariant de sécurité dérive.

