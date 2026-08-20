# ETF Fund Flows V1.0 — référentiel SHADOW

Date de référence : 20/08/2026

## 1. Objet

ETF Fund Flows V1 mesure les créations/rachats et variations organiques d'encours afin d'identifier l'accumulation, la distribution et les rotations de capitaux avant de considérer une éventuelle intégration aux modèles ETF PEA, Sector Rotation, Gold et Crypto.

Le module est **SHADOW_ONLY**. Son influence sur les décisions, scores de production, sizing, stops et ordres est strictement nulle.

## 2. Univers

### 2.1 ETF PEA

L'univers PEA est généré dynamiquement depuis le référentiel canonique de 102 ETF. La clé instrument est l'ISIN. Pour un ETF synthétique, la famille économique correspond à l'indice suivi et non au collatéral du swap.

Familles normalisées principales : `WORLD`, `ACWI`, `SP500`, `NASDAQ100`, `MSCI_EUROPE`, `EURO_STOXX_50`, `STOXX_EUROPE_600`, `CAC40`, `MSCI_EMU`, `EMERGING`, `JAPAN`.

### 2.2 Sentinelles externes

Le référentiel externe couvre :

- indices mondiaux et régionaux utiles au PEA ;
- technologie, semi-conducteurs, software, cybersécurité ;
- défense/aérospatial ;
- énergie, banques, santé, industriels, utilities, immobilier, matériaux ;
- uranium/nucléaire, cuivre, clean energy, IA/robotique ;
- or physique et mines d'or ;
- Bitcoin, Ethereum, Solana et staking ;
- un indicateur short Bitcoin conservé séparément du flux crypto long.

Les instruments inverse/leveraged sont exclus du score principal de rotation.

## 3. Sources et priorité

Ordre de priorité gouverné :

1. émetteur officiel — niveau A ;
2. source ETF spécialisée vérifiée — niveau A/B selon preuve ;
3. reconstruction émetteur — niveau B ;
4. Yahoo Finance — niveau C de secours ;
5. autres sources — niveau à documenter, jamais promotion implicite.

Une source moins forte peut compléter un champ absent d'une source plus forte le même jour. Dans ce cas, la confiance de l'observation fusionnée est abaissée au niveau de la source effectivement utilisée pour le calcul. Une source faible ne peut pas écraser une valeur plus forte.

## 4. Calcul des flux

Priorité :

`flow_t = (shares_t - shares_t-1) × NAV_t`

Si les parts ne sont pas disponibles :

`flow_t = AUM_t - AUM_t-1 × (1 + total_return_t)`

Le total return du fallback tient compte des distributions connues. Une simple variation d'AUM non corrigée de la performance n'est jamais assimilée à un fund flow.

## 5. Fenêtres et maturité

Fenêtres de recherche : 5, 20, 60 et 252 flux valides, plus YTD.

- moins de 20 flux calculables : `DATA_INSUFFICIENT` ;
- 20 à 59 : `PRELIMINARY_20_59` ;
- 60 et plus : `MATURE_60_PLUS`.

Le flux 1 jour est disponible pour diagnostic mais ne peut jamais déclencher une décision ou une promotion.

## 6. ETF Flow Score — EFS SHADOW

Pondérations pré-enregistrées :

- flow 5 jours : 15 % ;
- flow 20 jours : 15 % ;
- flow 60 jours : 10 % ;
- accélération : 20 % ;
- persistance : 15 % ;
- relatif aux pairs : 15 % ;
- confirmation flow/prix : 10 %.

Ces poids sont des hypothèses de recherche. Ils ne peuvent pas être retunés automatiquement sur les premiers résultats.

États indicatifs : `STRONG_OUTFLOW`, `OUTFLOW`, `NEUTRAL`, `MODERATE_INFLOW`, `STRONG_INFLOW`, `EXCEPTIONAL_ACCUMULATION`.

## 7. Overlay ETF PEA

Pour un ETF PEA mature :

- score propre : 50 % ;
- famille économique globale : 30 % ;
- famille PEA : 20 %.

Pour un historique jeune :

- score propre : 25 % ;
- famille globale : 45 % ;
- famille PEA : 30 %.

Cette règle permet à un ETF PEA World/S&P 500/Nasdaq-100 d'être contextualisé par les flux observés sur ses grands véhicules mondiaux sans confondre le lieu de cotation avec l'exposition économique.

## 8. Sector Rotation Flow Score — SRFS SHADOW

Pondérations pré-enregistrées :

- flux agrégé normalisé : 30 % ;
- breadth : 20 % ;
- accélération : 15 % ;
- confirmation régionale : 15 % ;
- persistance : 10 % ;
- confirmation prix : 10 %.

Sector Rotation V2.0 verrouillé PIT/OOS n'est pas modifié. SRFS est publié comme overlay séparé avec influence décisionnelle nulle.

## 9. Or et crypto

### Or

Composite SHADOW :

- or physique US : 50 % ;
- or physique Europe : 30 % ;
- mines d'or : 10 % ;
- confirmation prix : 10 %.

Aucune modification du modèle Gold V1.1 avant validation dédiée.

### Crypto

Les ETP/ETF Bitcoin, Ethereum, Solana et staking sont suivis par famille. Les produits short/inverse sont conservés dans un bloc spéculatif séparé et n'entrent pas dans le score crypto long.

Le fichier `CRYPTO_FUND_FLOW_WEEKLY_CONTROL.csv` permet d'intégrer un contrôle hebdomadaire externe de type CoinShares. Ce contrôle :

- est libellé en USD millions ;
- est validé sur date, source URL et confiance ;
- n'est jamais additionné aux flux ETF/ETP primaires ;
- a une influence décisionnelle nulle.

## 10. Protections anti-faux-signaux

- déduplication par instrument/date ;
- priorité de source explicite ;
- complément champ par champ sans écrasement silencieux ;
- données D/QUARANTINE non scorables ;
- dates futures interdites ;
- split ou reverse split probable mis en quarantaine ;
- valeurs structurelles/AUM non positives mises en quarantaine ;
- ETF inverse/leveraged exclus du score principal ;
- plusieurs devises : aucun montant absolu n'est additionné ; seuls les ratios normalisés restent comparables ;
- `flow_share_family` est également neutralisé pour une famille multi-devises ;
- aucune reconstitution historique depuis un snapshot courant ; l'historique démarre uniquement avec des observations PIT réellement persistées.

## 11. Exécution GitHub

### Quotidien

Workflow `etf_fund_flows_daily.yml`, jours ouvrés après clôture américaine :

1. restauration de `state/etf_fund_flows/` ;
2. tests ciblés ;
3. collecte ;
4. calcul des flux/overlays ;
5. sauvegarde de l'historique PIT ;
6. publication d'un artefact compact et d'une synthèse Android SHADOW.

### Hebdomadaire

`committee_master_daily.yml` restaure le même historique, exécute Fund Flows comme contexte SHADOW non bloquant, publie l'audit et conserve l'état. Une panne Fund Flows ne doit jamais altérer une décision canonique du Comité.

## 12. Outputs

- `outputs/etf_fund_flows/ETF_FLOW_INSTRUMENTS_SHADOW.csv`
- `outputs/etf_fund_flows/ETF_FLOW_FAMILIES_SHADOW.csv`
- `outputs/etf_fund_flows/SECTOR_ROTATION_FLOW_OVERLAY_V1.csv`
- `outputs/etf_fund_flows/TOP_PEA_FLOW_SHADOW.csv`
- `outputs/etf_fund_flows/TOP_OUTFLOWS_SHADOW.csv`
- `outputs/etf_fund_flows/GOLD_CRYPTO_FLOWS_SHADOW.json`
- `outputs/etf_fund_flows/CRYPTO_WEEKLY_EXTERNAL_CONTROL.json`
- `outputs/mobile/ETF_FUND_FLOWS_SHADOW.md`
- `outputs/audit/ETF_FUND_FLOW_V1_SHADOW.json`
- `outputs/gaps/ETF_FUND_FLOW_COLLECTION_FAILURES.csv`
- `state/etf_fund_flows/ETF_FUND_FLOW_OBSERVATIONS.csv`

## 13. Promotion

Aucune performance n'est attribuée à V1.0 avant accumulation d'un historique PIT suffisant. Toute promotion exige un protocole dédié PIT/OOS comparant au minimum :

- baseline sans flow versus challenger avec flow ;
- gain d'espérance et de profit factor ;
- drawdown et pertes de queue ;
- capacité à anticiper une rotation plutôt qu'à la suivre ;
- stabilité par régime, secteur, région et type d'ETF ;
- valeur marginale de chaque bloc ;
- absence de dégradation des modèles ETF MT, Sector Rotation, Gold et Crypto.

Le holdout global reste fermé. Aucun ordre réel.
