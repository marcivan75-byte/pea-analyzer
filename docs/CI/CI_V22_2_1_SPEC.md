# V22.2.1 — CI market orientation and governed entry gate

## Scope

V22.2.1 keeps WAVE09 disabled and retains the validated V22/V22.1/V22.2 selection pipeline. It adds a lightweight upstream market orientation and an entry-review overlay for shortlisted CI candidates only.

## Upstream market orientation

Only three indicators are collected:

- FRED `VIXCLS` (latest available daily close)
- CNN Fear & Greed Index
- VSTOXX (official STOXX symbol `V2TX`, bounded Yahoo quote/history fallbacks)

The three sources run concurrently. Cache TTL is 6 hours with a governed stale fallback up to 72 hours. The module has no dependency on WAVE09 and does not collect broad macro series.

## Regime rules

- VIX: `<17 RISK_ON`, `17–25 NEUTRAL`, `>25 RISK_OFF`
- CNN Fear & Greed: `<45 RISK_OFF`, `45–55 NEUTRAL`, `>55 RISK_ON`
- VSTOXX: `<20 RISK_ON`, `20–30 NEUTRAL`, `>30 RISK_OFF`
- CNN `>75` is additionally an overheat caution.

US orientation is the vote of VIX + CNN. Europe orientation is VSTOXX. Global orientation is the vote of all three.

## Entry gate

The market overlay is evaluated only after the existing V22.2 technical entry trigger:

1. A V22.2 `WAIT` can never be promoted by favorable macro context.
2. A V22.2 `READY_FOR_REVIEW` becomes `WAIT` when the relevant market scope is `RISK_OFF`.
3. `RISK_ON` or `NEUTRAL` allows CI review when the technical trigger is already valid.
4. CNN extreme greed (`>75`) becomes `CAUTION`; CI review then requires confidence >=70.
5. No automatic order is generated.

Actions use EUROPE orientation. ETFs use EUROPE when their metadata indicates Europe/Eurozone exposure; otherwise GLOBAL orientation.

## Confidence

The historical selection score remains unchanged. V22.2.1 produces a separate entry-confidence overlay:

- RISK_ON: +5 points
- NEUTRAL: 0
- RISK_OFF: -15
- CNN extreme greed: -5

The result is clipped to 0–100 and stored in `CI_CONFIDENCE_SCORE_V22_2_1`.

## Potential progression

`CI_POTENTIAL_UPSIDE_PCT` is explanatory and never changes selection or entry scoring.

Actions, in order:

1. existing consensus `upside_pct`
2. Yahoo consensus `upside_pct_yf`
3. target price vs last close
4. Yahoo mean target vs last close
5. technical potential to 52-week high

ETFs use technical potential to the 52-week high. `CI_POTENTIAL_METHOD` and `CI_POTENTIAL_REFERENCE_LEVEL` disclose the method and reference level.

## Outputs

- `outputs/committee_master/CI_MARKET_ORIENTATION_V22_2.csv`
- `outputs/committee_master/CI_ENTRY_WATCH_V22_2_1.csv`
- `outputs/mobile/ANDROID_CI_ENTRY_WATCH_V22_2_1.md`
- `outputs/audit/MARKET_ORIENTATION_V22_2.json`
- `outputs/audit/CI_ENTRY_WATCH_V22_2_1.json`
- `outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_1.json`

## Runtime design

The market orientation is three bounded requests executed concurrently and cached for six hours. It must remain a lightweight replacement for the small subset of WAVE09 functionality required for entry context, not a path to reintroduce WAVE09.

## Locked governance

- WAVE09 disabled
- selection criteria unchanged
- selection weights unchanged
- selection thresholds unchanged
- T1/T2 limited to ACTION TCT
- no real orders
- no automatic parameter promotion
