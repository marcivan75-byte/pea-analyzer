"""Longitudinal study of existing post-entry early-exit rules.

No threshold is learned from the holdout. The rule family and the numerical
parameters are the pre-existing FPEarlyExit defaults, frozen before evaluation.
2010-2022 and 2023-2026 are reported separately. Nothing is promoted here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.hebdo.fp_early_exit import FPEarlyExit
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import TabportAntiFP65k, add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import build_weekly_meta_signals
from v182.hebdo.tabport_regime_guard_development import load_ohlcv, DEV_END, HOLDOUT_START


RULE_SETS: dict[str, set[str] | None] = {
    "BASELINE": None,
    "STOP_ONLY_CONTROL": {"STOP"},
    "STOP_FAIL_FAST_J2": {"STOP", "FAIL_FAST_J2"},
    "STOP_STRUCTURE_ENTRY": {"STOP", "STRUCTURE_INVALID_ENTRY_DAY"},
    "STOP_MOM_DEAD_J3": {"STOP", "MOM_DEAD_J3"},
    "STOP_TRAIL_BE": {"STOP", "TRAIL_BE"},
    "STOP_CAPITULATION": {"STOP", "CAPITULATION"},
    "STOP_FAIL_FAST_STRUCTURE": {"STOP", "FAIL_FAST_J2", "STRUCTURE_INVALID_ENTRY_DAY"},
    "STOP_FAIL_FAST_STRUCTURE_MOM": {"STOP", "FAIL_FAST_J2", "STRUCTURE_INVALID_ENTRY_DAY", "MOM_DEAD_J3"},
    "FULL_LOCAL_NO_SECTOR": {"STOP", "FAIL_FAST_J2", "STRUCTURE_INVALID_ENTRY_DAY", "CAPITULATION", "MOM_DEAD_J3", "TRAIL_BE"},
}


def _run_antifp(signals: pd.DataFrame, prices_features: pd.DataFrame, cfg: TabportConfig, rules: set[str]) -> dict:
    engine = TabportAntiFP65k(cfg)
    engine.fp_exit = FPEarlyExit(
        stop_final=-cfg.stop_pct,
        fail_fast_j2=-0.025,
        enabled_rules=rules,
    )
    return engine.run(signals, prices_features)


def _control_check(base: pd.DataFrame, stop: pd.DataFrame) -> dict:
    """STOP_ONLY must reproduce the baseline closely enough to validate engines."""
    cols = ["ticker", "entry_date", "exit_date", "exit_reason", "return_net", "pnl_net"]
    if base.empty or stop.empty:
        raise ValueError("BLOCK_EARLY_EXIT_EMPTY_CONTROL_LEDGER")
    b = base[cols].copy().sort_values(["entry_date", "ticker", "exit_date"]).reset_index(drop=True)
    s = stop[cols].copy().sort_values(["entry_date", "ticker", "exit_date"]).reset_index(drop=True)
    same_count = len(b) == len(s)
    if not same_count:
        raise ValueError(f"BLOCK_EARLY_EXIT_STOP_CONTROL_COUNT:{len(b)}!={len(s)}")
    # Exit labels differ slightly (STOP_-9% vs STOP_FINAL_*), compare dates/returns instead.
    keys_equal = b[["ticker","entry_date","exit_date"]].astype(str).equals(s[["ticker","entry_date","exit_date"]].astype(str))
    max_return_delta = float(np.max(np.abs(pd.to_numeric(b["return_net"]) - pd.to_numeric(s["return_net"]))))
    max_pnl_delta = float(np.max(np.abs(pd.to_numeric(b["pnl_net"]) - pd.to_numeric(s["pnl_net"]))))
    if not keys_equal or max_return_delta > 1e-10 or max_pnl_delta > 1e-6:
        raise ValueError(f"BLOCK_EARLY_EXIT_STOP_CONTROL_MISMATCH keys={keys_equal} ret={max_return_delta} pnl={max_pnl_delta}")
    return {"same_trade_count": True, "same_entry_exit_keys": True, "max_return_delta": max_return_delta, "max_pnl_delta_eur": max_pnl_delta}


def run(pre2023: Path, manifest: Path, holdout_cache: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_ohlcv(pre2023, manifest, holdout_cache)
    signals, signal_audit = build_weekly_meta_signals(ohlcv)
    features = add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed, confirmation_audit = apply_j1_confirmation(signals, features)
    if confirmed.empty:
        raise ValueError("BLOCK_EARLY_EXIT_NO_CONFIRMED_SIGNALS")

    cfg = TabportConfig()
    base_prices = ohlcv[["date","ticker","open","high","low","close"]].copy()
    antifp_prices = features[["date","ticker","open","high","low","close","vol_z","rsi_14"]].copy()

    rows: list[dict] = []
    ledgers: list[pd.DataFrame] = []
    yearly: list[pd.DataFrame] = []
    quarterly: list[pd.DataFrame] = []
    base_ledger = None
    stop_ledger = None

    for model, rules in RULE_SETS.items():
        if model == "BASELINE":
            result = Tabport65k(cfg).run(confirmed, base_prices)
        else:
            result = _run_antifp(confirmed, antifp_prices, cfg, rules or {"STOP"})
        ledger = result["ledger"].copy()
        nav = result["equity"].copy()
        ledger["model"] = model
        ledgers.append(ledger)
        if model == "BASELINE": base_ledger = ledger.copy()
        if model == "STOP_ONLY_CONTROL": stop_ledger = ledger.copy()

        ledger["signal_date"] = pd.to_datetime(ledger["signal_date"], utc=True, errors="coerce")
        nav["date"] = pd.to_datetime(nav["date"], utc=True, errors="coerce")
        for segment, lo, hi in [
            ("DEVELOPMENT_2010_2022", pd.Timestamp("2010-01-01", tz="UTC"), DEV_END),
            ("HOLDOUT_2023_2026", HOLDOUT_START, pd.Timestamp("2100-01-01", tz="UTC")),
        ]:
            ls = ledger[(ledger["signal_date"] >= lo) & (ledger["signal_date"] <= hi)].copy()
            ns = nav[(nav["date"] >= lo) & (nav["date"] <= hi)].copy()
            rows.append({"model": model, "segment": segment, **overall_summary(ls, ns, initial_cash=cfg.initial_cash)})

        q = period_table(ledger, nav, "Q")
        y = period_table(ledger, nav, "Y")
        if not q.empty:
            q.insert(0, "model", model); quarterly.append(q)
        if not y.empty:
            y.insert(0, "model", model); yearly.append(y)

    if base_ledger is None or stop_ledger is None:
        raise ValueError("BLOCK_EARLY_EXIT_MISSING_CONTROL")
    control = _control_check(base_ledger, stop_ledger)

    segments = pd.DataFrame(rows)
    segments.to_csv(output_dir / "TABPORT_EARLY_EXIT_SEGMENTS.csv", index=False)
    pd.concat(ledgers, ignore_index=True).to_csv(output_dir / "TABPORT_EARLY_EXIT_LEDGERS.csv", index=False)
    pd.concat(yearly, ignore_index=True).to_csv(output_dir / "TABPORT_EARLY_EXIT_YEARLY.csv", index=False)
    pd.concat(quarterly, ignore_index=True).to_csv(output_dir / "TABPORT_EARLY_EXIT_QUARTERLY.csv", index=False)
    confirmed.to_csv(output_dir / "TABPORT_EARLY_EXIT_CONFIRMED.csv", index=False)
    confirmation_audit.to_csv(output_dir / "TABPORT_EARLY_EXIT_CONFIRMATION_AUDIT.csv", index=False)

    payload = {
        "status": "SUCCESS",
        "version": "TABPORT_EARLY_EXIT_FROZEN_V1",
        "governance": {
            "development": "2010-2022",
            "holdout": "2023-2026_EVALUATION_ONLY",
            "holdout_used_for_rule_or_threshold_selection": False,
            "candidate_family_frozen_before_holdout": True,
            "numerical_parameters": {"stop_final": -cfg.stop_pct, "fail_fast_j2": -0.025, "other_thresholds": "PREEXISTING_FPEarlyExit_CONSTANTS"},
            "post_entry_only": True,
            "synthetic_imputation": False,
            "production_promotion": False,
        },
        "control": control,
        "quality": quality,
        "signal_audit": signal_audit,
        "models": list(RULE_SETS),
    }
    (output_dir / "TABPORT_EARLY_EXIT_SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
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
