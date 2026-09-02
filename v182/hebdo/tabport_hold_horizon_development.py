"""Development-only holding-horizon study for TABPORT HEBDO AT META.

The same confirmed signals and portfolio rules are used for every candidate.
Only max_hold_sessions changes. A common per-ticker 252-session maturity filter
is applied before simulation so long horizons cannot benefit from EOP exits.
Horizon selection uses 2010-2022 only; 2023+ is evaluation-only.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import build_weekly_meta_signals
from v182.hebdo.tabport_regime_guard_development import load_ohlcv, DEV_END, HOLDOUT_START
from v182.hebdo.tabport_rerank_development import _objective

HORIZONS = {
    "H63": 63,
    "H126_BASELINE": 126,
    "H189": 189,
    "H252": 252,
}
MAX_HORIZON = max(HORIZONS.values())


def common_mature_cohort(confirmed: pd.DataFrame, ohlcv: pd.DataFrame, required_sessions: int = MAX_HORIZON) -> tuple[pd.DataFrame, dict]:
    """Keep a signal only when that ticker has enough bars after confirmation."""
    if required_sessions < 1:
        raise ValueError("BLOCK_HORIZON_INVALID_MATURITY")
    x = confirmed.copy()
    x["date"] = pd.to_datetime(x["date"], utc=True, errors="coerce")
    bars = ohlcv[["date", "ticker"]].copy()
    bars["date"] = pd.to_datetime(bars["date"], utc=True, errors="coerce")
    if x["date"].isna().any() or bars["date"].isna().any():
        raise ValueError("BLOCK_HORIZON_INVALID_DATE")
    dates_by_ticker = {
        str(t): tuple(g["date"].sort_values().drop_duplicates())
        for t, g in bars.groupby("ticker", sort=False)
    }
    future_counts = []
    for row in x.itertuples(index=False):
        dates = dates_by_ticker.get(str(row.ticker), ())
        future_counts.append(sum(d > row.date for d in dates))
    x["future_bars_after_confirmation"] = future_counts
    eligible = x["future_bars_after_confirmation"] >= required_sessions
    out = x.loc[eligible].copy().reset_index(drop=True)
    audit = {
        "signals_input": int(len(x)),
        "signals_common_mature": int(len(out)),
        "signals_excluded_insufficient_future_bars": int((~eligible).sum()),
        "required_future_bars_per_ticker": int(required_sessions),
        "common_min_signal_date": None if out.empty else str(out["date"].min()),
        "common_max_signal_date": None if out.empty else str(out["date"].max()),
    }
    if out.empty:
        raise ValueError("BLOCK_HORIZON_NO_COMMON_MATURE_SIGNALS")
    return out, audit


def _candidate_config(base: TabportConfig, horizon: int) -> TabportConfig:
    return replace(base, max_hold_sessions=int(horizon))


def run(pre2023: Path, manifest: Path, holdout_cache: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_ohlcv(pre2023, manifest, holdout_cache)
    signals, signal_audit = build_weekly_meta_signals(ohlcv)
    features = add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed, confirmation_audit = apply_j1_confirmation(signals, features)
    common, maturity_audit = common_mature_cohort(confirmed.reset_index(drop=True), ohlcv)
    common["date"] = pd.to_datetime(common["date"], utc=True, errors="coerce")
    prices = ohlcv[["date", "ticker", "open", "high", "low", "close"]].copy()
    base_cfg = TabportConfig()

    rows, ledgers, yearly_parts, quarterly_parts = [], [], [], []
    dev_scores = {}
    eop_counts = {}
    for model, horizon in HORIZONS.items():
        cfg = _candidate_config(base_cfg, horizon)
        result = Tabport65k(cfg).run(common, prices)
        ledger = result["ledger"].copy(); nav = result["equity"].copy()
        ledger["model"] = model; ledgers.append(ledger)
        ledger["signal_date"] = pd.to_datetime(ledger["signal_date"], utc=True, errors="coerce")
        nav["date"] = pd.to_datetime(nav["date"], utc=True, errors="coerce")
        eop = int(ledger["exit_reason"].astype(str).eq("EOP_DATA_END").sum())
        eop_counts[model] = eop
        if eop:
            raise ValueError(f"BLOCK_HORIZON_EOP_EXIT:{model}:{eop}")
        y = period_table(ledger, nav, "Y"); y.insert(0, "model", model); yearly_parts.append(y)
        q = period_table(ledger, nav, "Q"); q.insert(0, "model", model); quarterly_parts.append(q)
        dev_scores[model] = _objective(y)
        for segment, lo, hi in [
            ("DEVELOPMENT_2010_2022", pd.Timestamp("2010-01-01", tz="UTC"), DEV_END),
            ("HOLDOUT_2023_2026", HOLDOUT_START, pd.Timestamp("2100-01-01", tz="UTC")),
        ]:
            ls = ledger[(ledger["signal_date"] >= lo) & (ledger["signal_date"] <= hi)].copy()
            ns = nav[(nav["date"] >= lo) & (nav["date"] <= hi)].copy()
            rows.append({"model": model, "max_hold_sessions": horizon, "segment": segment, **overall_summary(ls, ns, initial_cash=cfg.initial_cash)})

    selected = max(dev_scores, key=dev_scores.get)
    pd.DataFrame(rows).to_csv(output_dir / "TABPORT_HOLD_HORIZON_SEGMENTS.csv", index=False)
    pd.concat(yearly_parts, ignore_index=True).to_csv(output_dir / "TABPORT_HOLD_HORIZON_YEARLY.csv", index=False)
    pd.concat(quarterly_parts, ignore_index=True).to_csv(output_dir / "TABPORT_HOLD_HORIZON_QUARTERLY.csv", index=False)
    pd.concat(ledgers, ignore_index=True).to_csv(output_dir / "TABPORT_HOLD_HORIZON_LEDGERS.csv", index=False)
    common.to_csv(output_dir / "TABPORT_HOLD_HORIZON_COMMON_COHORT.csv", index=False)
    confirmation_audit.to_csv(output_dir / "TABPORT_HOLD_HORIZON_CONFIRMATION_AUDIT.csv", index=False)
    payload = {
        "status": "SUCCESS",
        "version": "TABPORT_HOLD_HORIZON_DEV_ONLY_V1",
        "selected_on_development_only": selected,
        "development_objective": dev_scores,
        "horizons": HORIZONS,
        "maturity_audit": maturity_audit,
        "eop_exit_counts": eop_counts,
        "governance": {
            "fit_window": "2010-2022_ONLY",
            "holdout": "2023-2026_EVALUATION_ONLY_WHERE_252_SESSION_MATURE",
            "holdout_used_for_horizon_selection": False,
            "candidate_family_frozen_before_holdout": True,
            "common_signal_cohort": True,
            "per_ticker_full_maturity_required": True,
            "only_parameter_changed": "max_hold_sessions",
            "production_promotion": False,
            "synthetic_imputation": False,
        },
        "quality": quality,
        "signal_audit": signal_audit,
    }
    (output_dir / "TABPORT_HOLD_HORIZON_SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pre2023", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--holdout-cache", required=True)
    p.add_argument("--output-dir", required=True)
    a = p.parse_args()
    print(json.dumps(run(Path(a.pre2023), Path(a.manifest), Path(a.holdout_cache), Path(a.output_dir)), indent=2, default=str))


if __name__ == "__main__":
    main()
