"""Development-only fixed position sizing study for TABPORT.

Same signals, ranking, stop, exits, holding horizon and capacity constraints.
Only max_position_eur changes. Candidate family and objective are frozen before
holdout evaluation. 2010-2022 selects the size; 2023-2026 is evaluation only.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import build_weekly_meta_signals
from v182.hebdo.tabport_regime_guard_development import load_ohlcv, DEV_END, HOLDOUT_START

SIZES_EUR = {
    "SIZE_3000": 3000.0,
    "SIZE_3750": 3750.0,
    "SIZE_4500_BASELINE": 4500.0,
    "SIZE_5000": 5000.0,
    "SIZE_5400": 5400.0,
}


def _year_number(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.extract(r"(\d{4})", expand=False), errors="coerce")


def sizing_objective(yearly: pd.DataFrame, dev_summary: dict) -> float:
    """Reward stable annual return and positive years, penalize drawdown.

    This objective is fixed before holdout. All terms are development-only.
    """
    years = _year_number(yearly["periode"])
    y = yearly[(years >= 2011) & (years <= 2022)].copy()
    r = pd.to_numeric(y["rendement_portefeuille_pct"], errors="coerce").dropna()
    if r.empty:
        return -1e9
    dd = float(dev_summary.get("drawdown_max_pct", np.nan))
    if not np.isfinite(dd):
        return -1e9
    return float(r.median() - 0.35 * r.std(ddof=0) + 0.20 * (r > 0).mean() * 100.0 - 0.25 * abs(dd))


def candidate_config(base: TabportConfig, size_eur: float) -> TabportConfig:
    if not np.isfinite(size_eur) or size_eur <= 0 or size_eur > base.initial_cash:
        raise ValueError("BLOCK_SIZING_INVALID_POSITION_BUDGET")
    return replace(base, max_position_eur=float(size_eur))


def run(pre2023: Path, manifest: Path, holdout_cache: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_ohlcv(pre2023, manifest, holdout_cache)
    signals, signal_audit = build_weekly_meta_signals(ohlcv)
    features = add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed, confirmation_audit = apply_j1_confirmation(signals, features)
    if confirmed.empty:
        raise ValueError("BLOCK_SIZING_NO_CONFIRMED_SIGNALS")
    prices = ohlcv[["date", "ticker", "open", "high", "low", "close"]].copy()
    base = TabportConfig()

    rows: list[dict] = []
    yearly_parts: list[pd.DataFrame] = []
    quarterly_parts: list[pd.DataFrame] = []
    ledgers: list[pd.DataFrame] = []
    dev_scores: dict[str, float] = {}
    signal_counts: dict[str, int] = {}

    for model, size in SIZES_EUR.items():
        cfg = candidate_config(base, size)
        result = Tabport65k(cfg).run(confirmed, prices)
        ledger = result["ledger"].copy(); nav = result["equity"].copy()
        ledger["model"] = model; ledgers.append(ledger)
        ledger["signal_date"] = pd.to_datetime(ledger["signal_date"], utc=True, errors="coerce")
        nav["date"] = pd.to_datetime(nav["date"], utc=True, errors="coerce")
        y = period_table(ledger, nav, "Y"); y.insert(0, "model", model); yearly_parts.append(y)
        q = period_table(ledger, nav, "Q"); q.insert(0, "model", model); quarterly_parts.append(q)

        dev_ledger = ledger[ledger["signal_date"] <= DEV_END].copy()
        dev_nav = nav[nav["date"] <= DEV_END].copy()
        dev_summary = overall_summary(dev_ledger, dev_nav, initial_cash=cfg.initial_cash)
        dev_scores[model] = sizing_objective(y, dev_summary)
        signal_counts[model] = int(len(confirmed))

        for segment, lo, hi in [
            ("DEVELOPMENT_2010_2022", pd.Timestamp("2010-01-01", tz="UTC"), DEV_END),
            ("HOLDOUT_2023_2026", HOLDOUT_START, pd.Timestamp("2100-01-01", tz="UTC")),
        ]:
            ls = ledger[(ledger["signal_date"] >= lo) & (ledger["signal_date"] <= hi)].copy()
            ns = nav[(nav["date"] >= lo) & (nav["date"] <= hi)].copy()
            rows.append({"model": model, "position_budget_eur": size, "segment": segment,
                         **overall_summary(ls, ns, initial_cash=cfg.initial_cash)})

    if len(set(signal_counts.values())) != 1:
        raise ValueError("BLOCK_SIZING_SIGNAL_UNIVERSE_CHANGED")
    selected = max(dev_scores, key=dev_scores.get)
    pd.DataFrame(rows).to_csv(output_dir / "TABPORT_POSITION_SIZING_SEGMENTS.csv", index=False)
    pd.concat(yearly_parts, ignore_index=True).to_csv(output_dir / "TABPORT_POSITION_SIZING_YEARLY.csv", index=False)
    pd.concat(quarterly_parts, ignore_index=True).to_csv(output_dir / "TABPORT_POSITION_SIZING_QUARTERLY.csv", index=False)
    pd.concat(ledgers, ignore_index=True).to_csv(output_dir / "TABPORT_POSITION_SIZING_LEDGERS.csv", index=False)
    confirmed.to_csv(output_dir / "TABPORT_POSITION_SIZING_CONFIRMED.csv", index=False)
    confirmation_audit.to_csv(output_dir / "TABPORT_POSITION_SIZING_CONFIRMATION_AUDIT.csv", index=False)

    payload = {
        "status": "SUCCESS",
        "version": "TABPORT_FIXED_POSITION_SIZING_DEV_ONLY_V1",
        "selected_on_development_only": selected,
        "development_objective": dev_scores,
        "sizes_eur": SIZES_EUR,
        "governance": {
            "fit_window": "2010-2022_ONLY",
            "holdout": "2023-2026_EVALUATION_ONLY",
            "holdout_used_for_size_selection": False,
            "candidate_family_frozen_before_holdout": True,
            "same_signal_universe": True,
            "same_ranking": True,
            "same_stop": True,
            "same_exit_rules": True,
            "same_hold_horizon_sessions": base.max_hold_sessions,
            "only_parameter_changed": "max_position_eur",
            "objective_includes_drawdown_penalty": True,
            "production_promotion": False,
            "synthetic_imputation": False,
        },
        "quality": quality,
        "signal_audit": signal_audit,
    }
    (output_dir / "TABPORT_POSITION_SIZING_SUMMARY.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
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
