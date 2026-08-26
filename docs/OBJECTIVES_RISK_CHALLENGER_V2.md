# Objectifs/Risque Challenger V2 — shadow

Ce challenger ne modifie ni les scores ni les décisions de référence.

## Ordre de traitement

1. Gate Actions R/R brut : TCT 2,0 ; CT 2,5 ; MT 3,0, avec fiabilité minimale 65 %.
2. Ranking post-gate : 55 % sélection, 30 % R/R normalisé, 15 % fiabilité.
3. État `WATCH_WITH_TRIGGER` à partir de 60 en `RISK_ON`, 62 en `NEUTRAL`; aucun assouplissement en `RISK_OFF`.
4. Downside beta, drawdown et volatilité sont observés en challenger; une absence reste neutre.
5. Budget post-sélection : une famille économique, bêta cible 0,7–1,0, corrélation moyenne < 0,65, thème ≤ 30 %.
6. Promotion interdite avant huit semaines shadow et validation PIT/OOS.

## Hyper-critères

Les quinze positions de `HYPER_SELECTION_V1.json` correspondent dans l’ordre à TradingView, Boursorama,
RSI, potentiel central, qualité, croissance persistante, valeur/coût, bilan/tracking, risque global,
liquidité, révisions/flux, diversification, macro/secteur, catalyseurs et qualité des données.

La stabilité temporelle sur trois observations et la liquidité relative `rvol20 + spread` restent des
champs challenger à matérialiser; ils ne doivent pas être imputés artificiellement.
