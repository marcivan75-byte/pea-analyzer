# IPO Radar V1.2 — Deep Due Diligence

## Objectif

Améliorer la qualité des preuves avant cotation sans modifier les pondérations du modèle IPO V1.1 et sans autoriser de BUY automatique.

## Changements V1.2

- Inline XBRL du prospectus S-1/F-1/424B utilisé en priorité pour les données financières pré-IPO.
- SEC Company Facts conservé uniquement comme fallback lorsqu'une donnée financière utile n'est pas disponible dans le prospectus.
- Reconstruction des termes de l'offre : prix IPO, actions primaires, actions secondaires, actions post-offre, produit net, dilution par action.
- `opportunity_balance_sheet_post_ipo` n'est plus alimenté par un bilan pré-IPO : le score n'est calculé que lorsque le produit net de l'IPO est détecté.
- Calcul d'une borne supérieure de runway post-IPO avant utilisation planifiée des fonds.
- Le hard block `insufficient_12m_liquidity_post_offering` n'est déclenché par V1.2 que si même cette borne supérieure est inférieure à 1 an.
- Market cap implicite et Price/Sales IPO ajoutés comme diagnostics de valorisation absolue en shadow.
- La valorisation absolue ne renseigne jamais automatiquement le critère `valuation_vs_peers`/`risk_valuation` : celui-ci exige de vrais comparables.
- Nouveau fichier `IPO_DEEP_DD_EVIDENCE.csv` pour auditer la preuve utilisée par candidat.
- Nouveau brief `IPO_DEEP_DD_BRIEF.json` pour le comité.

## Gouvernance

- Pondérations Opportunité/Risque inchangées.
- Net score inchangé : 60 % opportunité / 40 % inverse du risque.
- Mode `SHADOW_ADVISORY_ONLY` conservé.
- `can_create_buy = false` et `live_orders_enabled = false`.
- T1/T2 restent interdits dans le module IPO.
- Toute promotion ou repondération reste conditionnée à un backtest PIT/OOS dédié.
