# V21.13.16 — weekly runtime install without editable package build

## Objective

Apply to the Friday weekly Committee workflow the same dependency-safe install optimization already validated on the daily workflow in V21.13.15.

## Change

The weekly workflow now:

- exposes `src` through `PYTHONPATH`;
- installs the exact production dependency mirror `requirements-runtime.txt`;
- keys the pip cache on `requirements-runtime.txt`;
- avoids building/installing the repository itself as an editable package.

## Invariants

The 13 production dependencies remain governed by the exact-equality guard introduced in V21.13.15. No dependency is removed.

The weekly execution sequence remains unchanged:

1. Action identity hydration;
2. unified Committee pipeline;
3. ETF structural-state replay;
4. Friday TCT/CT scoring;
5. Action CT V22.0/V22.1 + TCT V24.3.1 tactical bundle;
6. POSTMARKET V24.4.2 bundle;
7. decision brief;
8. ETF Fund Flows V1 SHADOW;
9. criteria-governance audit.

The compile/static validation gates, referential checks, state persistence, OHLCV cache protection, weekly research state and complete Committee artifact remain present.

## Financial scope

No scoring source, criterion, weight, threshold, universe, PIT rule, T1/T2 scope, PREMARKET/POSTMARKET candidate scope, or restitution is modified.
