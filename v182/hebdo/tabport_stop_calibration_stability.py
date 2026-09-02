"""Research-only stability audit for the existing stop-risk heuristic/calibration.

This module does not introduce a new model or tune on the 2023-2026 holdout.
It asks whether the existing PIT heuristic ``prob_stop_9`` has stable temporal
information before considering any calibrated use in production.

For each calendar year, calibration is fit only on rows available before the
purged training cutoff. Evaluation uses that year's already-mature outcomes.
2023-2026 use the same frozen pre-2023 training cutoff and are evaluation-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_longitudinal_audit73 import load_governed_ohlcv
from v182.hebdo.tabport_meta_component_ablation import StopRiskIsotonic, walkforward_variant
from v182.hebdo.tabport_meta_walkforward_research import (
    HOLDOUT_START,
    add_outcomes,
    build_pre_meta_candidates,
    training_cutoff_for_year,
)
from v182.hebdo.tabport_publish import build_weekly_meta_signals


def _valid_eval(frame: pd.DataFrame) -> pd.DataFrame:
    x = pd.to_numeric(frame.get("prob_stop_9"), errors="coerce")
    y = frame.get("hit_stop")
    valid = x.notna() & np.isfinite(x.to_numpy(dtype=float)) & y.isin([True, False])
    out = frame.loc[valid].copy()
    out["raw_score"] = x.loc[valid].astype(float)
    out["actual_stop"] = y.loc[valid].astype(bool).astype(int)
    return out


def _auc(y: pd.Series, p: pd.Series) -> float | None:
    if len(y) == 0 or y.nunique() < 2:
        return None
    return float(roc_auc_score(y.astype(int), p.astype(float)))


def _brier(y: pd.Series, p: pd.Series) -> float | None:
    if len(y) == 0:
        return None
    pp = np.clip(pd.to_numeric(p, errors="coerce").to_numpy(dtype=float), 0.0, 1.0)
    if not np.isfinite(pp).all():
        return None
    return float(brier_score_loss(y.astype(int).to_numpy(), pp))


def decile_table(eval_frame: pd.DataFrame, year: int, segment: str) -> pd.DataFrame:
    if eval_frame.empty:
        return pd.DataFrame()
    z = eval_frame[["raw_score", "actual_stop"]].copy()
    try:
        z["decile"] = pd.qcut(z["raw_score"], 10, labels=False, duplicates="drop")
    except ValueError:
        z["decile"] = 0
    out = z.groupby("decile", dropna=False).agg(
        n=("actual_stop", "size"),
        mean_raw_score=("raw_score", "mean"),
        actual_stop_rate=("actual_stop", "mean"),
    ).reset_index()
    out["year"] = int(year)
    out["segment"] = segment
    return out[["segment", "year", "decile", "n", "mean_raw_score", "actual_stop_rate"]]


def discrimination_by_year(labeled: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    metric_rows: list[dict] = []
    deciles: list[pd.DataFrame] = []
    years = sorted(int(x) for x in labeled["date"].dt.year.unique())
    for year in years:
        segment = "DEVELOPMENT_2010_2022" if year < 2023 else "HOLDOUT_2023_2026"
        cutoff = training_cutoff_for_year(year)
        train = labeled[labeled["date"] <= cutoff].copy()
        eval_year = _valid_eval(labeled[labeled["date"].dt.year == year].copy())
        model = StopRiskIsotonic()
        fit = model.fit(train) if len(train) else model.audit()
        calibrated = None
        if fit.get("status") == "CALIBRATED_TEMPORAL_OOS" and not eval_year.empty:
            tmp = eval_year.copy()
            tmp["prob_stop_9"] = tmp["raw_score"]
            tmp = model.transform(tmp)
            calibrated = pd.to_numeric(tmp["prob_stop_9"], errors="coerce")
        y = eval_year["actual_stop"] if not eval_year.empty else pd.Series(dtype=int)
        raw = eval_year["raw_score"] if not eval_year.empty else pd.Series(dtype=float)
        if calibrated is None:
            calibrated = pd.Series(np.nan, index=eval_year.index, dtype=float)
        d = decile_table(eval_year, year, segment)
        if not d.empty:
            deciles.append(d)
            mono = d[["mean_raw_score", "actual_stop_rate"]].corr(method="spearman").iloc[0, 1] if len(d) > 1 else np.nan
        else:
            mono = np.nan
        metric_rows.append({
            "segment": segment,
            "year": year,
            "training_cutoff": str(cutoff),
            "training_rows": int(len(train)),
            "training_status": fit.get("status"),
            "training_event_rate": fit.get("event_rate"),
            "eval_rows": int(len(eval_year)),
            "eval_event_rate": None if len(y) == 0 else float(y.mean()),
            "raw_auc": _auc(y, raw),
            "calibrated_auc": _auc(y, calibrated.dropna()) if calibrated.notna().all() else None,
            "raw_brier": _brier(y, raw),
            "calibrated_brier": _brier(y, calibrated) if calibrated.notna().all() else None,
            "decile_spearman": None if pd.isna(mono) else float(mono),
            "holdout_training_frozen": bool(year >= 2023),
        })
    return pd.DataFrame(metric_rows), (pd.concat(deciles, ignore_index=True) if deciles else pd.DataFrame())


def _run_period(signals: pd.DataFrame, ohlcv: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    s = signals[(signals["date"] >= start) & (signals["date"] < end)].copy()
    if s.empty:
        return {"status": "EMPTY", "trades": 0}
    needed = set(s["ticker"].astype(str))
    prices = ohlcv[ohlcv["ticker"].astype(str).isin(needed)][["date", "ticker", "open", "high", "low", "close"]].copy()
    result = Tabport65k(TabportConfig()).run(s, prices)
    m = result["metrics"].copy()
    m["status"] = "OK"
    return m


def portfolio_by_year(ohlcv: pd.DataFrame, candidates: pd.DataFrame, labeled: pd.DataFrame) -> pd.DataFrame:
    baseline, _ = build_weekly_meta_signals(ohlcv)
    stop_scored, _ = walkforward_variant(
        candidates, labeled, use_meta=False, use_stop_cal=True, variant="STOP_CAL_ONLY"
    )
    tickers = set(baseline["ticker"].astype(str)) | set(stop_scored["ticker"].astype(str))
    features = add_antifp_features(ohlcv[ohlcv["ticker"].astype(str).isin(tickers)].copy())
    baseline_confirmed, _ = apply_j1_confirmation(baseline, features)
    stop_confirmed, _ = apply_j1_confirmation(stop_scored, features)
    rows: list[dict] = []
    for year in range(2010, 2027):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        segment = "DEVELOPMENT_2010_2022" if year < 2023 else "HOLDOUT_2023_2026"
        for model_name, sig in [("BASELINE_UNTRAINED", baseline_confirmed), ("STOP_CAL_ONLY", stop_confirmed)]:
            m = _run_period(sig, ohlcv, start, end)
            m.update({"segment": segment, "year": year, "model": model_name})
            rows.append(m)
    out = pd.DataFrame(rows)
    return out


def run(pre2023: Path, manifest: Path, holdout_cache: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_governed_ohlcv(pre2023, manifest, holdout_cache)
    candidates, candidate_audit = build_pre_meta_candidates(ohlcv)
    labeled = add_outcomes(candidates, ohlcv)
    metrics, deciles = discrimination_by_year(labeled)
    portfolio = portfolio_by_year(ohlcv, candidates, labeled)

    dev_metrics = metrics[metrics["segment"] == "DEVELOPMENT_2010_2022"].copy()
    eligible = dev_metrics[dev_metrics["raw_auc"].notna()].copy()
    dev_auc_gt_half = int((eligible["raw_auc"] > 0.5).sum()) if len(eligible) else 0
    dev_auc_gt_55 = int((eligible["raw_auc"] > 0.55).sum()) if len(eligible) else 0
    dev_brier_improved = int((eligible["calibrated_brier"] < eligible["raw_brier"]).sum()) if len(eligible) else 0
    dev_monotonic_positive = int((eligible["decile_spearman"] > 0).sum()) if len(eligible) else 0

    piv = portfolio.pivot(index=["segment", "year"], columns="model", values=["return_pct", "profit_factor", "max_drawdown", "trades"]).reset_index()
    flat = []
    for col in piv.columns:
        if isinstance(col, tuple):
            flat.append("_".join(str(x) for x in col if str(x)))
        else:
            flat.append(str(col))
    piv.columns = flat
    dev_port = piv[piv["segment"] == "DEVELOPMENT_2010_2022"].copy()
    pf_better = int((dev_port.get("profit_factor_STOP_CAL_ONLY", pd.Series(dtype=float)) > dev_port.get("profit_factor_BASELINE_UNTRAINED", pd.Series(dtype=float))).sum())
    ret_better = int((dev_port.get("return_pct_STOP_CAL_ONLY", pd.Series(dtype=float)) > dev_port.get("return_pct_BASELINE_UNTRAINED", pd.Series(dtype=float))).sum())
    dd_better = int((dev_port.get("max_drawdown_STOP_CAL_ONLY", pd.Series(dtype=float)) > dev_port.get("max_drawdown_BASELINE_UNTRAINED", pd.Series(dtype=float))).sum())

    summary = {
        "status": "SUCCESS",
        "version": "TABPORT_STOP_CALIBRATION_STABILITY_V1",
        "production_promotion": False,
        "governance": {
            "development": "EXPANDING_WALK_FORWARD_2010_2022",
            "holdout": "2023_2026_EVALUATION_ONLY",
            "holdout_training_frozen_before_2023": True,
            "holdout_used_for_tuning": False,
            "new_features_added": False,
            "stop_score_source": "EXISTING_PROB_STOP_9_HEURISTIC_ONLY",
            "calibration": "ISOTONIC_MONOTONIC_REMAP_ONLY",
            "same_signal_family": True,
            "same_j1_confirmation": True,
            "same_stop_pct": 0.09,
            "same_hold_horizon_sessions": 126,
            "same_position_budget_eur": 4500,
            "synthetic_imputation": False,
        },
        "quality": quality,
        "candidate_audit": candidate_audit,
        "labeled_rows": int(len(labeled)),
        "development_stability": {
            "eligible_auc_years": int(len(eligible)),
            "raw_auc_gt_0_50_years": dev_auc_gt_half,
            "raw_auc_gt_0_55_years": dev_auc_gt_55,
            "calibrated_brier_better_years": dev_brier_improved,
            "positive_decile_monotonicity_years": dev_monotonic_positive,
            "portfolio_pf_better_years": pf_better,
            "portfolio_return_better_years": ret_better,
            "portfolio_drawdown_better_years": dd_better,
        },
    }
    metrics.to_csv(output_dir / "TABPORT_STOP_CAL_YEARLY_DISCRIMINATION.csv", index=False)
    deciles.to_csv(output_dir / "TABPORT_STOP_CAL_DECILES.csv", index=False)
    portfolio.to_csv(output_dir / "TABPORT_STOP_CAL_YEARLY_PORTFOLIO.csv", index=False)
    piv.to_csv(output_dir / "TABPORT_STOP_CAL_YEARLY_DELTAS.csv", index=False)
    (output_dir / "TABPORT_STOP_CAL_STABILITY_SUMMARY.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print("---DISCRIMINATION---")
    print(metrics.to_csv(index=False))
    print("---PORTFOLIO---")
    print(portfolio.to_csv(index=False))
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pre2023", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--holdout-cache", required=True)
    p.add_argument("--output-dir", required=True)
    a = p.parse_args()
    run(Path(a.pre2023), Path(a.manifest), Path(a.holdout_cache), Path(a.output_dir))


if __name__ == "__main__":
    main()
