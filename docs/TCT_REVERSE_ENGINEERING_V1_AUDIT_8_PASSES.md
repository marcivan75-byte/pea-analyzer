# TCT Reverse Engineering V1 — Audit 8 passes

## Statut

Branche de recherche : `agent/tct-reverse-engineering-v1`.
Base de référence conservée intacte : TCT V24.4.2.

Cette branche ne modifie pas la décision TCT de production. Elle construit un moteur indépendant de rétro-engineering des hausses fortes afin d'identifier les facteurs observables avant le mouvement.

## Objectif

Identifier, sans look-ahead, les configurations qui précèdent des hausses de 10 %, 15 %, 20 %, 25 % et 30 % dans des horizons de 5, 10, 15 et 20 séances, avec priorité au label cœur `+25 % en <=20 séances`.

L'exécution est définie au prochain open après la date d'observation J0. Les fenêtres terminales incomplètes sont censurées et ne sont jamais classées perdantes par défaut.

## Huit passes d'audit

### PASS 01 — Univers / identité
- unicité `instrument_id + date` ;
- absence de clés manquantes ;
- aucune promotion si l'univers historique PEA n'est pas suffisamment qualifié.

### PASS 02 — Labels / exécution
- entrée réaliste au prochain open ;
- labels 10/15/20/25/30 % ;
- horizons 5/10/15/20 séances ;
- MFE/MAE futures uniquement comme outcomes, jamais comme features.

### PASS 03 — PIT / look-ahead
- interdiction stricte de toute feature contenant `fwd_`, `future_`, `label_`, `target_`, `outcome_`, `mfe_`, `mae_`, `exit_` ou `realized_` ;
- snapshots qualitatifs rattachés en `merge_asof(... direction=backward)` ;
- conflit sur une observation PIT déjà historisée = erreur bloquante.

### PASS 04 — Qualité des features
- couverture minimale ;
- absence d'infinis ;
- calculs techniques transparents : momentum, MM, RSI, MACD, ATR, bandes, breakouts et RVOL.

### PASS 05 — Split / leakage
- découpage chronologique ;
- purge conservatrice avant chaque frontière ;
- aucun apprentissage ou découverte sur le holdout.

### PASS 06 — Robustesse des patterns
- support minimal obligatoire ;
- lift calculé contre la fréquence de base ;
- combinaisons limitées à 1–3 facteurs pour éviter l'explosion combinatoire et l'overfit.

### PASS 07 — Réalisme trading
- prochain open ;
- MFE/MAE disponibles ;
- coût de transaction explicitement paramétré ;
- aucune exécution réelle : recherche uniquement.

### PASS 08 — OOS / holdout
- présence des blocs Discovery / Development / Validation / Holdout ;
- holdout verrouillé pour la découverte ;
- promotion impossible tant qu'un gate échoue.

## Consensus, objectifs et catalyseurs

Le collecteur Finnhub existant est un cache d'état courant utile à la production, mais il ne constitue pas à lui seul un historique de révisions exploitable pour une étude rétroactive. Le moteur V1 ajoute donc :

- un store append-only horodaté ;
- des features as-of ;
- deltas absolus et relatifs à 5/10/20 séances ;
- pivot des observations long-form existantes ;
- événements catalyseurs uniquement s'ils étaient déjà observables à J0.

Aucune donnée qualitative 2026 ne doit être rétroinjectée artificiellement dans 2020–2025.

## Profondeur historique réelle

Le référentiel OHLCV V21.13 disponible dans le dépôt indique :

- fenêtre gouvernée : 2020-01-01 → 2026-08-20 ;
- 1 449 instruments qualifiés pour 2020–2022 ;
- 1 687 instruments qualifiés à partir de 2023 ;
- historique 2010–2019 non présent dans ce socle.

Conséquence : le moteur accepte une base plus longue lorsqu'elle sera réellement reliée, mais il ne doit jamais annoncer une validation 2010–2018 sur le cache actuel.

## Sorties attendues du runner

`scripts/run_tct_reverse_engineering_v1.py` produit :

- `research_matrix.parquet` ;
- `factor_quantiles_discovery.csv` ;
- `boolean_patterns_discovery.csv` ;
- `audit_8_passes.csv` ;
- `status.json`.

Le code de sortie est 0 uniquement si les huit gates passent. Sinon il retourne 2 et `promotion_allowed=false`.

## Règle de gouvernance

Une réussite des huit audits d'ingénierie ne prouve pas l'existence d'alpha. Elle prouve seulement que la recherche est menée avec un protocole cohérent. La promotion d'une règle TCT exige ensuite :

1. stabilité Development ;
2. confirmation Validation ;
3. holdout final non retuné ;
4. fréquence suffisante ;
5. coûts et capacité acceptables ;
6. absence de dépendance à un seul régime ou quelques titres.
