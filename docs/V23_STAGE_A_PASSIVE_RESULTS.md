# V23 Stage A — Passive benchmark result

## Frozen primary benchmark

CW8.PA (Amundi MSCI World Swap UCITS ETF EUR Acc) is the primary passive PEA proxy frozen before any V23 stock-picking research.

Evaluation window: 2010-01-04 to 2022-12-30. The observed 2023–2026 V22.1 holdout is not used.

Execution assumptions: EUR 65,000 initial capital, integer shares, 0.20% fee per side; stress adds 0.10% slippage per side.

## Base result

- Initial capital: EUR 65,000
- Final liquidation: EUR 240,852.10
- Net profit: EUR 175,852.10
- Cumulative net return: +270.54%
- CAGR: +10.61%/yr
- Max drawdown: -33.60%
- Annualized volatility: 14.81%
- Capital utilization at entry: 99.86%

## Stress result

- Final liquidation: EUR 240,546.42
- Net profit: EUR 175,546.42
- Cumulative net return: +270.07%
- CAGR: +10.60%/yr
- Max drawdown: -33.61%
- CAGR delta vs base: -0.011 percentage point/yr

## Interpretation

This establishes the minimum economic hurdle for V23. A future active model cannot be promoted merely because it is profitable. It must add meaningful net value versus this passive baseline and/or materially improve risk-adjusted performance and drawdown without relying on repeated validation mining.

The previous V22.1 pre-2023 backtest is historical context only and is not a V23 selection reference. Its much stronger in-sample result cannot be treated as reliable evidence after the final out-of-sample failure.

## Governance

- benchmark fixed before V23 stock-picking tests: yes
- 2023–2026 used for benchmark selection or rule tuning: no
- passive variants tested: 1
- result source: GitHub Actions run 33473109041, artifact v23-benchmark-stage-a-33473109041
