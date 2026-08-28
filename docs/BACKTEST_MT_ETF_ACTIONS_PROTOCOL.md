# Backtests futurs — ETF MT et Actions MT

Date de verrouillage : 2026-08-28
Statut : protocoles lockés **avant résultats**. Research only. Influence décision = 0.

## Périmètre

| Univers | Protocole | Signal | Top K | Horizon primaire |
|---|---|---|---|---|
| ETF MT | `config/ETF_MT_PIT_OOS_PROTOCOL.json` | `ETF_MT_SCORE` (fallback O/R shadow) | 5 | 60 séances |
| Actions MT | `config/ACTION_MT_PIT_OOS_PROTOCOL.json` | `ACTION_MT_SCORE` (fallback O/R shadow) | 10 | 60 séances |

Secondaires : 20 et 120 séances. T1/T2 interdits sur les deux univers MT.

## Fenêtres (identiques au Sector Rotation V2)

- VALIDATION_OOS : 2026-09-01 → 2026-12-31
- DIAGNOSTIC_OOS : 2027-01-01 → 2027-04-30
- Holdout final : à partir du 2027-05-01 (**fermé**, ignoré)

## Exécution PIT

- Entrée = première séance **strictement après** la date de signal
- Interdit : close du jour du signal
- Prix = cache OHLCV yfinance auto-adjust
- Baseline = equipondéré de l’univers éligible du snapshot
- Espacement mini des snapshots : 10 jours
- Historique mini : 250 séances

## Promotion (aucun auto-retuning)

8 snapshots indépendants **par** période OOS, edge vs équipondéré ≥ 0,5 pp, taux positif et P10 non dégradés au-delà des seuils lockés. Même si les gates OOS passent : `promotion_ready = false` tant que le holdout n’a pas son propre protocole de suivi.

## Données à constituer (pas encore là)

Déposer plus tard, sans changer les JSON de protocole :

- `state/backtest/ETF_MT_PIT_OBSERVATIONS.csv`
- `state/backtest/ACTION_MT_PIT_OBSERVATIONS.csv`

Colonnes mini : `isin;as_of;ETF_MT_SCORE|ACTION_MT_SCORE;forward_return_pct_60d`

Harnais : `python -m v182.backtest.mt_pit_oos`
Sorties : `outputs/audit/ETF_MT_PIT_OOS_STATUS.json` et `ACTION_MT_PIT_OOS_STATUS.json`.

Tant que les CSV sont absents, le statut reste `WAIT_FOR_PIT_HISTORY`.
