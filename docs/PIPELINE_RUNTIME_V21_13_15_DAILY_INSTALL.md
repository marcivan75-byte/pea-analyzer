# V21.13.15 — daily runtime install without editable package build

## Objective

Reduce scheduled daily GitHub runtime without changing financial logic, data, criteria, weights, thresholds, PIT rules, outputs, or production dependencies.

## Change

The daily workflow no longer needs to build/install the repository itself as an editable package before execution. It now:

- exposes `src` through `PYTHONPATH`;
- installs `requirements-runtime.txt`;
- keys the pip cache on `requirements-runtime.txt`.

`requirements-runtime.txt` is an exact ordered mirror of `[project].dependencies` in `pyproject.toml`.

## Safety contract

A regression test requires exact equality between the runtime requirements file and the 13 production dependencies in `pyproject.toml`. The daily execution sequence remains:

1. collection and enrichment;
2. ETF structural replay;
3. daily TCT/CT scoring;
4. Action CT V22.0/V22.1 + TCT V24.3.1 tactical bundle;
5. POSTMARKET V24.4.2 bundle.

No financial configuration or scoring source is modified.

## Scope

V21.13.15 applies only to the daily workflow. The weekly workflow remains unchanged until the daily pattern has passed the full CI matrix. This preserves WIP=1 and makes rollback trivial.
