# Backtests futurs — ETF MT et Actions MT

Date de verrouillage : 2026-08-28
Statut protocoles : lockés **avant résultats**.
Statut données : cache OHLCV du ZIP 2026-08-27 réutilisé ; observations reconstruites **non promotion-eligible**.

## Ce qui a été complété depuis le fichier existant

Cache ZIP `data/cache/actions` + `data/cache/etf` :
- 2023-01-02 → 2026-08-24, 930 séances
- Actions : 1790 tickers, 94,6 % de closes non nuls
- ETF : 102 tickers, 84,0 % de closes non nuls

Reconstruction PIT (prix only) :
- signal = momentum 126 séances **≤ as_of**
- forward 60 séances **strictement après as_of**
- 105 dates, pas de 10 jours, jusqu’à as_of ≤ cache_end - 90j
- 173 436 lignes Actions / 8 588 lignes ETF

Ce n’est **pas** le score MT de production. `promotion_eligible = false`.
Les fenêtres officielles OOS commencent le 2026-09-01 : cette reconstruction est du diagnostic pré-OOS.

## Protocoles

`config/ETF_MT_PIT_OOS_PROTOCOL.json` et `config/ACTION_MT_PIT_OOS_PROTOCOL.json`

Harnais : `python -m v182.backtest.mt_pit_oos`
Reconstructeur : `python -m v182.backtest.reconstruct_mt_pit_from_ohlcv` (si le cache local est présent).
