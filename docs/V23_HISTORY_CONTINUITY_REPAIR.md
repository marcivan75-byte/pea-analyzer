# V23 — History continuity repair and multi-exchange execution audit

Status: **CLOSED — audit completed**

This note closes the V23 data-continuity / tradability workstream that was opened after the frozen cross-sectional momentum baseline encountered non-executable historical paths. It is a data/simulator audit, not a strategy-parameter optimization.

## Governance

- V23 signal rule remained frozen: classic 12–1 cross-sectional momentum, top 10 monthly, equal-weight target.
- No 2023–2026 observation was used to select, tune, weight or threshold the strategy.
- Canonical master history was **not mutated**. Repairs are generated as a pre-2023 overlay with provenance.
- All repair mappings had to pass fail-closed identity gates against an independent public price history before rows could be appended.
- The historical universe still has **survivorship bias**. This repair does not remove that limitation.
- The reconstruction is price-only and is not a general corporate-action-complete security master.

## Root cause found

The master parquet had real continuity holes around the end of 2019. Several securities remained listed and had complete independent histories through 2020–2022, while their rows stopped in the master at 2019-12-30/31.

The first tradability audit also revealed that requiring every European holding and target to quote on one common execution date is incorrect: different exchange calendars can legitimately differ by one or more sessions. The v3 atomic-common-date simulator was therefore rejected.

## Repair overlay

Run `33490682895` completed successfully after extending the diagnosed whitelist to nine securities. The repair overlay added **6,863 rows**. Every accepted mapping passed the same gates:

- at least 400 overlapping observations,
- overlap close correlation >= 0.9999,
- median master/source close ratio within 0.1% of 1.0,
- relative MAD <= 0.1%,
- at least 100 missing rows to patch.

All nine actually passed much more strongly: **correlation 1.0, median ratio 1.0, relative MAD 0.0**.

| ISIN | Security | Independent ticker | Master last date | Patched rows |
|---|---|---|---:|---:|
| NO0010708068 | Vow ASA | VOW.OL | 2019-12-30 | 757 |
| NO0010598683 | Hofseth BioCare ASA | HBC.OL | 2019-12-30 | 757 |
| IT0001469995 | Digital Bros S.p.A. | DIB.MI | 2019-12-30 | 767 |
| NO0010159684 | Medistim ASA | MEDI.OL | 2019-12-30 | 757 |
| NL0000334118 | ASM International N.V. | ASM.AS | 2019-12-31 | 772 |
| DK0060520450 | Napatech A/S | NAPA.OL | 2019-12-30 | 757 |
| FR0011716265 | Crossject SA | ALCJ.PA | 2019-12-31 | 772 |
| IT0003895668 | Eurotech S.p.A. | ETH.MI | 2019-12-30 | 767 |
| NO0010205966 | Navamedic ASA | NAVA.OL | 2019-12-30 | 757 |

## Post-repair tradability

The frozen monthly top-10 signal set contains 1,450 selected rows across 145 months. On the repaired overlay:

- executable within 1 calendar day: 703 / 1,450 = 48.48%,
- within 3 days: 1,348 / 1,450 = 92.97%,
- within 5 days: 1,437 / 1,450 = 99.10%,
- within 10 days: 1,440 / 1,450 = 99.31%,
- median delay: 2 days,
- 95th percentile: 4 days,
- maximum non-null delay: 6 days.

The only ten rows with no later pre-2023 quote are the ten selections generated on **2022-12-30**, the terminal observation of the evaluation period. They are therefore right-censored by design rather than unexplained execution gaps.

For the audited signal path, no non-terminal top-10 selection remains without an executable quote inside the declared 10-calendar-day bound.

## Correct causal execution model

The accepted v4 simulator uses two-stage multi-exchange execution:

1. after a month-end signal, each old holding is sold on its own first available quote strictly after the signal;
2. once all sales are complete, actual cash is known and equal target notional is frozen;
3. each new target is bought on its own first quote strictly after that readiness date;
4. every leg is fail-closed at a maximum 10-calendar-day delay;
5. the terminal 2022-12-30 signal is censored rather than using 2023 data.

This correction changes execution mechanics only. It does **not** alter top-N, lookback, skip period, ranking, weights or any strategy parameter.

The successful run completed **144 rebalances**, with maximum signal-to-complete delay of **7 calendar days** and mean delay **3.39 days**. This also provides an end-to-end check that no executed buy/sell leg in the tested path remains blocked by a >10-day continuity hole.

## Frozen momentum result after repair

### Base costs — 0.20% per side

- Initial capital: EUR 65,000
- Final equity: **EUR 427,101.73**
- Net profit: **EUR 362,101.73**
- Cumulative net return: **+557.08%**
- CAGR: **15.60%**
- Max drawdown: **-43.00%**
- Annualized volatility: **28.74%**
- Fees: **EUR 97,003.89**
- Trade actions: 2,822

Frozen passive CW8 over the full pre-2023 period:

- CAGR: **10.61%**
- cumulative net return: **+270.54%**
- max drawdown: **-33.60%**
- annualized volatility: **14.81%**

Thus the frozen momentum rule has positive full-period net alpha in this dataset, but at substantially greater drawdown and roughly double the volatility.

### Stress — +0.10% slippage per side

- Final equity: **EUR 315,127.45**
- Cumulative net return: **+384.81%**
- CAGR: **12.93%**
- Max drawdown: **-43.42%**
- Annualized volatility: **28.75%**
- Fees: **EUR 78,114.63**

It still exceeds the frozen full-period passive CAGR under the declared stress, but the excess falls sharply from about +4.99 percentage points/year to about +2.33 points/year. The strategy is therefore materially cost/slippage sensitive.

## Stability test

The result is not stable across the two predeclared chronological halves:

| Period | Momentum CAGR | Momentum stress CAGR | Momentum max DD |
|---|---:|---:|---:|
| 2010–2016 | **9.92%** | **7.38%** | -43.00% |
| 2017–2022 | **21.52%** | **18.71%** | -38.14% |

For an apples-to-apples passive CW8 buy-and-hold calculation using the already frozen CW8 price series and the same 0.20%/side fee convention, the corresponding approximate subperiod CAGRs are:

- 2010–2016: **12.64%**, max DD about -21.79%,
- 2017–2022: **8.20%**, max DD about -33.56%.

Therefore momentum **underperforms passive materially in 2010–2016** while dominating it in 2017–2022. The apparent full-period alpha is highly regime-dependent.

## Decision

**Data-continuity / execution audit: PASS for the diagnosed strategy path, with explicit limitations.** The nine diagnosed continuity holes are repaired by a provenance-controlled overlay and the v4 simulator now completes causally without unexplained non-terminal execution gaps.

**Frozen 12–1 top-10 momentum as a promotable core strategy: NOT VALIDATED.** Despite a strong full-period CAGR, it fails the V23 robustness requirement because:

1. it loses to passive CW8 in the 2010–2016 chronological subperiod;
2. its drawdown and volatility are substantially worse than passive;
3. stress costs remove a large part of the apparent alpha;
4. the universe retains survivorship bias, making the positive result optimistic;
5. the price-only master is not demonstrated to be globally complete outside the diagnosed executed path.

No lookback, skip, top-N, rebalance-frequency or weighting optimization is authorized on this already observed pre-2023 result. Doing so would recreate the V22 validation-mining failure mode.

## Closure

This WIP is closed. The repaired overlay and causal execution model may be retained as infrastructure, but the 12–1 momentum hypothesis must not be tuned on these results. Any next V23 research workstream must test a genuinely different PIT information source or a new predeclared hypothesis under non-recycled chronological validation.
