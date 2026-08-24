# V4 Hebdo — audit 3/5 — critères, pondérations et gates

Date: 2026-08-24

## Périmètre

- Cohérence des 633 critères Actions, 268 critères ETF et de tous les vecteurs actifs.
- Concentration des pondérations et sensibilité descriptive des seuils.
- Gate de sélection, qualité Boursorama, règle Morningstar ETF et timing TradingView.
- Suppression d'Investing du chemin de décision V4.

## Constats

1. Les neuf vecteurs actifs sont normalisés et finis. Leur nombre effectif de critères va de 6,06 à 30,17; les trois premiers poids représentent de 17 % à 58 % selon le vecteur.
2. Aucun champ Boursorama, TradingView ou Investing n'appartient aux vecteurs de score. Les sources post-sélection ont donc une influence de score nulle par construction.
3. Le snapshot ne contient aucun jeu de résultats futur, point-in-time et hors échantillon permettant d'estimer honnêtement une nouvelle pondération ou un nouveau seuil.
4. Le gate V22.2.2 était couplé à Investing et son correctif ETF reposait sur un monkey-patch temporaire.
5. Une règle analystes Actions appliquée aux ETF serait conceptuellement fausse.

## Corrections

- Nouveau gate autonome `ci_selection_gate_v4.py`, directement branché sur V22.2.1 et sans import du résolveur Investing.
- Seuils centralisés : score 77, confiance 66 et potentiel de consensus Actions 20 %, bornes inclusives.
- Actions : consensus de potentiel valide, puis Boursorama `BUY/STRONG_BUY`; `HOLD`, absence ou valeur inconnue restent en attente; `SELL/STRONG_SELL` rejettent la qualité d'entrée.
- ETF : score et confiance communs, Morningstar au moins 3 étoiles; aucun consensus, nombre d'analystes ou potentiel Boursorama requis.
- TradingView : TCT→journalier, CT→hebdomadaire, MT→mensuel. `BUY/STRONG_BUY` confirme, `NEUTRAL` attend, `SELL/STRONG_SELL` bloque l'entrée et ouvre une revue de sortie si la ligne est détenue.
- Toute donnée technique absente produit `WAIT_SOURCE_MISSING`, jamais un signal baissier.
- Audit de calibration descriptif; aucune optimisation n'est promue sans preuve PIT/OOS.

## Validation

- Gouvernance renforcée : **69/69 PASS**.
- Audit de calibration : **6/6 PASS**.
- Tests ciblés du gate, des seuils et des classes d'actifs : **14 PASS**.
- Ruff : **PASS**.
- Grille descriptive sur 15 candidats gelés : données complètes, aucune modification automatique de seuil.

Décision : conserver les pondérations et seuils de référence. C'est un gel gouverné, non une validation statistique de performance future.

Statut de l'itération: **PASS**.
