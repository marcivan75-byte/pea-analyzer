"""Development-only regime-guard research for TABPORT HEBDO AT META.

Thresholds are learned exclusively from 2010-2022 confirmed signals. They are
then frozen and evaluated unchanged on 2023-2026 holdout. The holdout is never
used to choose thresholds or combinations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.hebdo.meta_price_history import load_pre2023_development, load_holdout
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_longitudinal_audit73 import _quality_filter
from v182.hebdo.tabport_publish import build_weekly_meta_signals

DEV_END = pd.Timestamp("2022-12-31", tz="UTC")
HOLDOUT_START = pd.Timestamp("2023-01-01", tz="UTC")


def load_ohlcv(pre2023: Path, manifest: Path, holdout_cache: Path) -> tuple[pd.DataFrame, list[dict]]:
    dev_raw = load_pre2023_development(pre2023, manifest)
    hold_raw = load_holdout(holdout_cache)
    dev, a = _quality_filter(dev_raw, "DEVELOPMENT_2010_2022")
    hold, b = _quality_filter(hold_raw, "HOLDOUT_2023_2026")
    if (dev["date"] > DEV_END).any() or (hold["date"] < HOLDOUT_START).any():
        raise ValueError("BLOCK_REGIME_GUARD_SEGMENT_CONTAMINATION")
    combined = pd.concat([dev, hold], ignore_index=True)
    dup = combined.duplicated(["date", "ticker"], keep=False)
    if dup.any():
        combined = combined.loc[~dup].copy()
    return combined.sort_values(["ticker", "date"]).reset_index(drop=True), [a, b]


def learn_thresholds(confirmed: pd.DataFrame) -> dict[str, float]:
    x = confirmed.copy()
    x["date"] = pd.to_datetime(x["date"], utc=True, errors="coerce")
    dev = x[x["date"] <= DEV_END].copy()
    if dev.empty:
        raise ValueError("BLOCK_REGIME_GUARD_NO_DEVELOPMENT_SIGNALS")
    required = ["vol_z", "prob_stop_9", "atr_14_pct"]
    if any(c not in dev.columns for c in required):
        raise ValueError("BLOCK_REGIME_GUARD_MISSING_FEATURE")
    # Quantiles are intentionally coarse and fixed before holdout inspection.
    return {
        "vol_z_max_q60": float(pd.to_numeric(dev["vol_z"], errors="coerce").quantile(0.60)),
        "prob_stop_9_max_q50": float(pd.to_numeric(dev["prob_stop_9"], errors="coerce").quantile(0.50)),
        "atr_14_pct_min_q70": float(pd.to_numeric(dev["atr_14_pct"], errors="coerce").quantile(0.70)),
    }


def masks(confirmed: pd.DataFrame, thresholds: dict[str, float]) -> dict[str, pd.Series]:
    vol = pd.to_numeric(confirmed["vol_z"], errors="coerce")
    stop = pd.to_numeric(confirmed["prob_stop_9"], errors="coerce")
    atr = pd.to_numeric(confirmed["atr_14_pct"], errors="coerce")
    m_vol = vol.le(thresholds["vol_z_max_q60"])
    m_stop = stop.le(thresholds["prob_stop_9_max_q50"])
    m_atr = atr.ge(thresholds["atr_14_pct_min_q70"])
    return {
        "BASELINE": pd.Series(True, index=confirmed.index),
        "VOL_Z_LE_DEV_Q60": m_vol,
        "PROB_STOP_LE_DEV_Q50": m_stop,
        "ATR_GE_DEV_Q70": m_atr,
        "VOL_Z_LE_Q60_AND_STOP_LE_Q50": m_vol & m_stop,
        "VOL_Z_LE_Q60_AND_ATR_GE_Q70": m_vol & m_atr,
    }


def summarize_segment(ledger: pd.DataFrame, nav: pd.DataFrame, initial_cash: float) -> dict:
    return overall_summary(ledger, nav, initial_cash=initial_cash)


def run(pre2023: Path, manifest: Path, holdout_cache: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_ohlcv(pre2023, manifest, holdout_cache)
    signals, signal_audit = build_weekly_meta_signals(ohlcv)
    features = add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed, confirmation_audit = apply_j1_confirmation(signals, features)
    confirmed = confirmed.reset_index(drop=True)
    confirmed["date"] = pd.to_datetime(confirmed["date"], utc=True, errors="coerce")
    thresholds = learn_thresholds(confirmed)
    model_masks = masks(confirmed, thresholds)
    cfg = TabportConfig()
    prices = ohlcv[["date", "ticker", "open", "high", "low", "close"]].copy()

    rows = []
    ledgers = []
    quarterly = []
    yearly = []
    for model, mask in model_masks.items():
        chosen = confirmed.loc[mask.fillna(False)].copy()
        if chosen.empty:
            continue
        result = Tabport65k(cfg).run(chosen, prices)
        ledger = result["ledger"].copy(); nav = result["equity"].copy()
        ledger["model"] = model; ledgers.append(ledger)
        ledger["signal_date"] = pd.to_datetime(ledger["signal_date"], utc=True, errors="coerce")
        nav["date"] = pd.to_datetime(nav["date"], utc=True, errors="coerce")

        for segment, lo, hi in [
            ("DEVELOPMENT_2010_2022", pd.Timestamp("2010-01-01", tz="UTC"), DEV_END),
            ("HOLDOUT_2023_2026", HOLDOUT_START, pd.Timestamp("2100-01-01", tz="UTC")),
        ]:
            ls = ledger[(ledger["signal_date"] >= lo) & (ledger["signal_date"] <= hi)].copy()
            ns = nav[(nav["date"] >= lo) & (nav["date"] <= hi)].copy()
            sm = summarize_segment(ls, ns, cfg.initial_cash)
            rows.append({"model": model, "segment": segment, "signals_selected": int(((chosen["date"] >= lo) & (chosen["date"] <= hi)).sum()), **sm})

        q = period_table(ledger, nav, "Q")
        if not q.empty:
            q.insert(0, "model", model); quarterly.append(q)
        y = period_table(ledger, nav, "Y")
        if not y.empty:
            y.insert(0, "model", model); yearly.append(y)

    summary = pd.DataFrame(rows)
    summary.to_csv(output_dir / "TABPORT_REGIME_GUARD_SEGMENTS.csv", index=False)
    pd.concat(ledgers, ignore_index=True).to_csv(output_dir / "TABPORT_REGIME_GUARD_LEDGERS.csv", index=False)
    if quarterly:
        pd.concat(quarterly, ignore_index=True).to_csv(output_dir / "TABPORT_REGIME_GUARD_QUARTERLY.csv", index=False)
    if yearly:
        pd.concat(yearly, ignore_index=True).to_csv(output_dir / "TABPORT_REGIME_GUARD_YEARLY.csv", index=False)
    confirmed.to_csv(output_dir / "TABPORT_REGIME_GUARD_CONFIRMED_SIGNALS.csv", index=False)
    confirmation_audit.to_csv(output_dir / "TABPORT_REGIME_GUARD_CONFIRMATION_AUDIT.csv", index=False)

    payload = {
        "status": "SUCCESS",
        "version": "TABPORT_REGIME_GUARD_DEV_ONLY_V1",
        "thresholds": thresholds,
        "governance": {
            "threshold_fit_window": "2010-2022_ONLY",
            "holdout_window": "2023-2026",
            "holdout_used_for_threshold_selection": False,
            "synthetic_imputation": False,
            "production_promotion": False,
            "candidate_family_frozen_before_holdout_evaluation": True,
        },
        "quality": quality,
        "signal_audit": signal_audit,
        "models": sorted(model_masks),
    }
    (output_dir / "TABPORT_REGIME_GUARD_SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pre2023", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--holdout-cache", required=True)
    p.add_argument("--output-dir", required=True)
    a = p.parse_args()
    payload = run(Path(a.pre2023), Path(a.manifest), Path(a.holdout_cache), Path(a.output_dir))
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
