"""Research-only PIT ablation of META probability and stop-risk calibration.

Purpose: isolate the effect of the two probabilistic components behind TABPORT
ranking without changing production. 2010-2022 is expanding walk-forward
research; 2023-2026 is evaluation-only with all training frozen before 2023.

Variants:
- BASELINE_UNTRAINED: current published fallback chain.
- META_ONLY: trained MetaLabeler, heuristic stop risk remains uncalibrated.
- STOP_CAL_ONLY: prob_meta=0.5, isotonic calibration of the existing PIT
  heuristic prob_stop_9 against realized hit_stop.
- META_STOP_CAL: both components trained/calibrated.

No new signal filter, stop, holding horizon, sizing rule or portfolio constraint
is introduced.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from v182.hebdo.expected_value_ranker import ExpectedValueRanker, MAE_VALID_STATUS
from v182.hebdo.meta_labeler import MetaLabeler
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_longitudinal_audit73 import load_governed_ohlcv
from v182.hebdo.tabport_meta_walkforward_research import (
    EMBARGO_DAYS,
    HOLDOUT_START,
    add_outcomes,
    build_pre_meta_candidates,
    training_cutoff_for_year,
)
from v182.hebdo.tabport_publish import build_weekly_meta_signals


class StopRiskIsotonic:
    """Calibrate the existing heuristic stop score without inventing features."""

    def __init__(self):
        self.model: IsotonicRegression | None = None
        self.status = "UNTRAINED"
        self.n = 0
        self.event_rate = None

    def fit(self, train: pd.DataFrame) -> dict:
        need = {"prob_stop_9", "hit_stop"}
        missing = need - set(train.columns)
        if missing:
            raise ValueError(f"BLOCK_STOP_CAL_MISSING:{sorted(missing)}")
        x = pd.to_numeric(train["prob_stop_9"], errors="coerce")
        y = train["hit_stop"]
        valid = x.notna() & np.isfinite(x.to_numpy(dtype=float)) & y.isin([True, False])
        x = x.loc[valid].astype(float)
        y = y.loc[valid].astype(bool).astype(int)
        self.n = int(len(x))
        self.event_rate = None if self.n == 0 else float(y.mean())
        if self.n < 100:
            self.status = "BLOCK_STOP_CAL_INSUFFICIENT_ROWS"
            return self.audit()
        counts = y.value_counts()
        if set(counts.index) != {0, 1} or int(counts.min()) < 20:
            self.status = "BLOCK_STOP_CAL_INSUFFICIENT_CLASSES"
            return self.audit()
        if int(x.nunique()) < 10:
            self.status = "BLOCK_STOP_CAL_DEGENERATE_SCORE"
            return self.audit()
        self.model = IsotonicRegression(out_of_bounds="clip", increasing=True)
        self.model.fit(x.to_numpy(), y.to_numpy())
        self.status = MAE_VALID_STATUS
        return self.audit()

    def audit(self) -> dict:
        return {"status": self.status, "n": self.n, "event_rate": self.event_rate}

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.model is None or self.status != MAE_VALID_STATUS:
            out = frame.copy()
            return out
        out = frame.copy()
        raw = pd.to_numeric(out["prob_stop_9"], errors="coerce")
        if raw.isna().any() or not np.isfinite(raw.to_numpy(dtype=float)).all():
            raise ValueError("BLOCK_STOP_CAL_NONFINITE_SCORE")
        calibrated = np.asarray(self.model.transform(raw.to_numpy(dtype=float)), dtype=float)
        if not np.isfinite(calibrated).all() or ((calibrated < 0) | (calibrated > 1)).any():
            raise ValueError("BLOCK_STOP_CAL_INVALID_PROBABILITY")
        out["prob_stop_9_raw_heuristic"] = raw.to_numpy(dtype=float)
        out["prob_stop_9"] = calibrated
        out["mae_model_status"] = MAE_VALID_STATUS
        return out


def _fit_meta(train: pd.DataFrame) -> tuple[MetaLabeler, dict]:
    model = MetaLabeler(label_horizon_days=EMBARGO_DAYS)
    result = {"status": "BLOCK_NO_TRAINING_ROWS", "n": 0}
    if len(train):
        result = model.train(train)
    return model, result


def walkforward_variant(
    candidates: pd.DataFrame,
    labeled: pd.DataFrame,
    *,
    use_meta: bool,
    use_stop_cal: bool,
    variant: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    parts: list[pd.DataFrame] = []
    audits: list[dict] = []
    for year in sorted(candidates["date"].dt.year.unique()):
        year = int(year)
        score = candidates[candidates["date"].dt.year == year].copy()
        cutoff = training_cutoff_for_year(year)
        train = labeled[labeled["date"] <= cutoff].copy()

        meta_model, meta_result = _fit_meta(train)
        meta_trained = meta_result.get("status") == "TRAINED_PURGED_TEMPORAL_OOS"
        stop_model = StopRiskIsotonic()
        stop_result = stop_model.fit(train) if len(train) else stop_model.audit()
        stop_trained = stop_result.get("status") == MAE_VALID_STATUS

        for decision, grp in score.groupby("date", sort=True):
            s = grp.copy()
            if use_meta and meta_trained:
                s = meta_model.predict_proba(s)
            else:
                s["prob_meta"] = 0.5
                s["meta_model_status"] = (
                    meta_result.get("status", "UNTRAINED") if use_meta else "ABLATION_META_FIXED_0_5"
                )
            if use_stop_cal and stop_trained:
                s = stop_model.transform(s)
            ranked = ExpectedValueRanker().rank_batch(s)
            ranked["date"] = decision
            ranked["wf_train_cutoff"] = cutoff
            ranked["wf_train_n"] = len(train)
            ranked["wf_meta_trained"] = bool(meta_trained)
            ranked["wf_stop_calibrated"] = bool(stop_trained)
            ranked["ablation_variant"] = variant
            parts.append(ranked)

        audits.append(
            {
                "variant": variant,
                "year": year,
                "score_rows": int(len(score)),
                "training_cutoff": str(cutoff),
                "training_rows": int(len(train)),
                "meta_training_status": meta_result.get("status"),
                "stop_training_status": stop_result.get("status"),
                "stop_training_event_rate": stop_result.get("event_rate"),
                "holdout_training_frozen": bool(year >= 2023),
            }
        )

    if not parts:
        raise ValueError(f"BLOCK_META_ABLATION_NO_SIGNALS:{variant}")
    out = pd.concat(parts, ignore_index=True)
    out = out[
        out["tier"].isin(["TCT", "CT_WATCH"])
        & (pd.to_numeric(out["EV_net"], errors="coerce") >= 0)
    ].copy()
    out = out.sort_values(["date", "EV_net", "ticker"], ascending=[True, False, True]).reset_index(drop=True)
    return out, pd.DataFrame(audits)


def _segment_run(signals: pd.DataFrame, ohlcv: pd.DataFrame, start, end) -> dict:
    s = signals.copy()
    if start is not None:
        s = s[s["date"] >= start]
    if end is not None:
        s = s[s["date"] < end]
    if s.empty:
        return {"status": "EMPTY"}
    needed = set(s["ticker"].astype(str))
    prices = ohlcv[ohlcv["ticker"].astype(str).isin(needed)][["date", "ticker", "open", "high", "low", "close"]].copy()
    result = Tabport65k(TabportConfig()).run(s, prices)
    m = result["metrics"].copy()
    m["status"] = "OK"
    return m


def run(pre2023: Path, manifest: Path, holdout_cache: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_governed_ohlcv(pre2023, manifest, holdout_cache)
    baseline, baseline_audit = build_weekly_meta_signals(ohlcv)
    candidates, candidate_audit = build_pre_meta_candidates(ohlcv)
    labeled = add_outcomes(candidates, ohlcv)

    definitions = {
        "META_ONLY": (True, False),
        "STOP_CAL_ONLY": (False, True),
        "META_STOP_CAL": (True, True),
    }
    variants: dict[str, pd.DataFrame] = {"BASELINE_UNTRAINED": baseline}
    audit_parts = []
    for name, (use_meta, use_stop) in definitions.items():
        scored, audit = walkforward_variant(
            candidates, labeled, use_meta=use_meta, use_stop_cal=use_stop, variant=name
        )
        variants[name] = scored
        audit_parts.append(audit)

    feature_tickers = set().union(*(set(x["ticker"].astype(str)) for x in variants.values()))
    features = add_antifp_features(ohlcv[ohlcv["ticker"].isin(feature_tickers)].copy())
    confirmed: dict[str, pd.DataFrame] = {}
    j1_parts = []
    for name, signals in variants.items():
        c, j1 = apply_j1_confirmation(signals, features)
        confirmed[name] = c
        jj = j1.copy(); jj["model"] = name; j1_parts.append(jj)

    rows = []
    for name, s in confirmed.items():
        for segment, start, end in [
            ("DEVELOPMENT_2010_2022", None, HOLDOUT_START),
            ("HOLDOUT_2023_2026", HOLDOUT_START, None),
        ]:
            m = _segment_run(s, ohlcv, start, end)
            m.update({"model": name, "segment": segment})
            rows.append(m)
    comparison = pd.DataFrame(rows)
    train_audit = pd.concat(audit_parts, ignore_index=True)

    summary = {
        "status": "SUCCESS",
        "version": "TABPORT_META_COMPONENT_ABLATION_V1",
        "production_promotion": False,
        "governance": {
            "development": "EXPANDING_WALK_FORWARD_2010_2022",
            "label_embargo_days": EMBARGO_DAYS,
            "holdout": "2023_2026_EVALUATION_ONLY",
            "holdout_training_frozen_before_2023": True,
            "holdout_used_for_tuning": False,
            "same_signal_family": True,
            "same_fp_filter": True,
            "same_j1_confirmation": True,
            "same_stop_pct": 0.09,
            "same_hold_horizon_sessions": 126,
            "same_position_budget_eur": 4500,
            "stop_calibration_features": ["prob_stop_9_heuristic_only"],
            "synthetic_imputation": False,
        },
        "quality": quality,
        "baseline_audit": baseline_audit,
        "candidate_audit": candidate_audit,
        "labeled_training_rows": int(len(labeled)),
        "variants": list(variants),
    }
    comparison.to_csv(output_dir / "TABPORT_META_COMPONENT_COMPARISON.csv", index=False)
    train_audit.to_csv(output_dir / "TABPORT_META_COMPONENT_TRAIN_AUDIT.csv", index=False)
    pd.concat(j1_parts, ignore_index=True).to_csv(output_dir / "TABPORT_META_COMPONENT_J1_AUDIT.csv", index=False)
    for name, frame in variants.items():
        cols = [c for c in ["date", "ticker", "EV_net", "prob_meta", "meta_model_status", "prob_stop_9", "prob_stop_9_raw_heuristic", "mae_model_status", "wf_train_cutoff", "wf_train_n", "wf_meta_trained", "wf_stop_calibrated"] if c in frame.columns]
        frame[cols].to_csv(output_dir / f"TABPORT_META_COMPONENT_SIGNALS_{name}.csv", index=False)
    (output_dir / "TABPORT_META_COMPONENT_SUMMARY.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print("---COMPARISON---")
    print(comparison.to_csv(index=False))
    print("---TRAIN---")
    print(train_audit.to_csv(index=False))
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pre2023", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--holdout-cache", required=True)
    p.add_argument("--output-dir", required=True)
    a = p.parse_args()
    run(Path(a.pre2023), Path(a.manifest), Path(a.holdout_cache), Path(a.output_dir))


if __name__ == "__main__":
    main()
