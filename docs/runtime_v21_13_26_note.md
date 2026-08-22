# V21.13.26 — audit state reuse

Only `WAVE_00_ETF_TICKERS`, `WAVE_01_ACTION_OHLCV`, and `WAVE_02_ETF_OHLCV` may automatically reuse the immediately preceding complete collection inventory because these stages do not mutate the Action or ETF master DataFrames or the retained provenance ledger. Each wave still writes its own audit artifact, failures, source context, and history row. Any later wave recomputes the inventory normally, and `WAVE_99_FINAL` remains a full recomputation.
