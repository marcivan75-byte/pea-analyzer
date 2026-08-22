# ETF Fund Flows V1.1 — périmètre PEA SHADOW

Date de mise à jour : 22/08/2026 — V21.13.7

## Objet

ETF Fund Flows mesure les créations/rachats et variations organiques d'encours
pour contextualiser les ETF PEA et Sector Rotation. Le module reste
`SHADOW_ONLY` : influence décisionnelle, score de production, sizing, stops,
promotion et ordres réels sont à zéro.

Les fonctions, univers, contrôles et sorties Gold et Crypto/ETP ont été retirés.
Le module ne peut plus collecter ni publier ces classes d'actifs.

## Univers

- 102 ETF PEA issus du référentiel canonique ;
- sentinelles ETF externes non Gold/non Crypto servant exclusivement à la
  contextualisation des familles, régions et secteurs PEA ;
- clé instrument : ISIN ;
- instruments inverse/leveraged exclus du score principal.

Pour un ETF synthétique, la famille économique correspond à l'indice suivi et
non au collatéral du swap.

## Sources et intégrité

Priorité gouvernée : émetteur officiel A, source ETF vérifiée A/B,
reconstruction émetteur B, Yahoo Finance C de secours. Une source faible ne
peut pas écraser une valeur plus forte.

Le calcul préfère :

`flow_t = (shares_t - shares_t-1) × NAV_t`

À défaut :

`flow_t = AUM_t - AUM_t-1 × (1 + total_return_t)`

Le fallback tient compte des distributions connues. Une variation brute d'AUM
n'est jamais assimilée à un flux. Les valeurs sans date explicite, les conflits
temporels, les dates futures, splits probables et données non positives suivent
les règles fail-closed V21.16.

## Fenêtres et scores SHADOW

Fenêtres : 5, 20, 60 et 252 flux valides, plus YTD.

- moins de 20 flux calculables : `DATA_INSUFFICIENT` ;
- 20 à 59 : `PRELIMINARY_20_59` ;
- 60 et plus avec fenêtre complète : `MATURE_60_PLUS`.

EFS conserve ses poids pré-enregistrés : 15 % flow 5j, 15 % flow 20j,
10 % flow 60j, 20 % accélération, 15 % persistance, 15 % relatif aux pairs et
10 % confirmation flow/prix.

SRFS conserve : 30 % flux agrégé, 20 % breadth, 15 % accélération,
15 % confirmation régionale, 10 % persistance et 10 % confirmation prix.
Sector Rotation V2 n'est pas retuné ; SRFS reste un overlay séparé.

## Exécution

- `etf_fund_flows_daily.yml` : diagnostic manuel uniquement ;
- `committee_master_daily.yml` : cadence hebdomadaire unique ;
- cache PIT : `state/etf_fund_flows/ETF_FUND_FLOW_OBSERVATIONS.csv` ;
- une panne du module SHADOW ne peut pas altérer une décision canonique.

## Sorties actives

- `outputs/etf_fund_flows/ETF_FLOW_INSTRUMENTS_SHADOW.csv`
- `outputs/etf_fund_flows/ETF_FLOW_FAMILIES_SHADOW.csv`
- `outputs/etf_fund_flows/SECTOR_ROTATION_FLOW_OVERLAY_V1.csv`
- `outputs/etf_fund_flows/TOP_PEA_FLOW_SHADOW.csv`
- `outputs/etf_fund_flows/TOP_OUTFLOWS_SHADOW.csv`
- `outputs/mobile/ETF_FUND_FLOWS_SHADOW.md`
- `outputs/audit/ETF_FUND_FLOW_V1_SHADOW.json`
- `outputs/gaps/ETF_FUND_FLOW_COLLECTION_FAILURES.csv`

## Promotion

Aucune performance n'est attribuée avant un historique PIT suffisant et une
validation PIT/OOS dédiée. Le holdout reste fermé et aucun ordre réel n'est
autorisé.
