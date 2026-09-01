# V23 Stage A — Simple active baseline result

## Frozen rule

One predeclared variant only:

- instrument: CW8.PA
- signal frequency: monthly
- invested when adjusted close > 200-day SMA AND 12-month momentum > 0
- signal observed at close; execution no earlier than next trading session
- otherwise cash with 0% return
- initial capital: EUR 65,000
- fee: 0.20% per side
- stress slippage: +0.10% per side
- evaluation: 2010-01-01 to 2022-12-30 only

No 2023–2026 data were accessed and no parameter search was performed.

## Result

| Metric | Passive CW8 | Simple trend/momentum | Simple stress |
|---|---:|---:|---:|
| CAGR | 10.61% | 4.85% | 4.66% |
| Cumulative net return | 270.54% | 84.91% | 80.65% |
| Final liquidation | EUR 240,852 | EUR 120,192 | EUR 117,423 |
| Max drawdown | -33.60% | -24.90% | -25.66% |
| Annualized volatility | 14.81% | 10.82% | 10.83% |
| Exposure | ~100% | 71.04% | 71.04% |
| Round-trip entries | 1 | 12 | 12 |

The simple rule reduced drawdown by about 8.7 percentage points and volatility by about 4.0 points, but surrendered about 5.76 percentage points of annual CAGR. On these pre-2023 data, the risk reduction is not sufficient to compensate for the return loss; even simple return/drawdown and return/volatility ratios favor the passive benchmark.

## Decision

REJECT as a superior core strategy. Retain only as evidence that naive market timing can lower drawdown while materially destroying return after costs.

This result is deliberately not followed by tuning SMA length, momentum horizon, rebalance frequency, or signal logic on the same evaluation period. That would repeat the validation-mining error diagnosed in V22.1.

## Provenance

- Passive source: GitHub Actions run 33473109041
- Simple baseline source: GitHub Actions run 33473268594
- Simple artifact: v23-simple-baseline-33473268594
