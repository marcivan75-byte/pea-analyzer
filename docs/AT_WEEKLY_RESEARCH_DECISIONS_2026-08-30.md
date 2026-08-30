# AT Weekly research decisions — 2026-08-30

Status: RESEARCH ONLY — branch `research/at-weekly-v1-20260829`.

## Invariants (LOCKED)

- Entry models remain fixed: `OPT_CONT_07_05` and `OPT_CONT_15_12`.
- Strategic weekly exits use completed-week information only and execute at next-week open.
- A protective stop is a standing order known from entry and is independent of weekly strategic signals.
- Fixed stop execution contract:
  - if weekly open <= stop level, fill at actual weekly open (gap-through);
  - else if weekly low <= stop level, fill at stop level;
  - otherwise no protective-stop exit.
- `ENDPOINT_MARK` is valuation only, never an execution.
- No same-bar trailing reconstruction from weekly high/low without intrabar ordering.
- No lookahead and no optimistic gap fill.

## Validated V7 evidence

GitHub Actions run `33290230246` completed successfully with the protective-stop contract tests enabled. Universe: 1,739 valid actions. Combined sample: 208 trades for each tested stop family.

| Protective stop | Max loss | P10 | PF | Reward/Risk | Mean return | Losses <= -10% | Losses <= -15% |
|---|---:|---:|---:|---:|---:|---:|---:|
| none (anchor) | -45.690% | -15.873% | 1.589 | 2.452 | +3.096% | 31 | 22 |
| 5% | -8.333% | -5.000% | 1.232 | 3.469 | +0.789% | 0 | 0 |
| 7% | -8.333% | -7.000% | 1.366 | 2.834 | +1.375% | 0 | 0 |
| 9% | -9.786% | -9.000% | 1.585 | 2.887 | +2.430% | 0 | 0 |
| 12% | -13.061% | -12.000% | 1.733 | 3.027 | +3.356% | 53 | 0 |

The -8.333% worst outcome under 5%/7% is compatible with the locked gap-through rule; a standing stop cannot guarantee the stop percentage when the market opens below it.

## Decision D-STOP-01 — LOCKED FOR NEXT RESEARCH STAGE

Use a **9% standing protective stop** as the default protective layer for the next strategic-exit research stage.

Rationale:

- eliminates every observed loss <= -10% in the validated V7 sample;
- reduces max loss from -45.690% to -9.786%;
- retains PF 1.585 versus 1.589 for the unprotected anchor;
- improves combined Reward/Risk from 2.452 to 2.887;
- retains mean return +2.430%, materially better than the 5% and 7% stops;
- 12% has higher PF/mean but permits 53 trades at or below -10%, inconsistent with the current risk-first objective.

This is a **research selection, not a production/order rule**. It must remain fixed while strategic exits are compared. Reopening this stop choice requires new evidence, not indicator tuning.

## Next WIP=1 chantier

With the protective layer fixed at 9%, compare strategic weekly exit rules only. No entry-model changes and no stop optimization are allowed during that comparison. Selection must be based on both entry families and multiple time windows, with sample-size safeguards and tail-risk reporting.
