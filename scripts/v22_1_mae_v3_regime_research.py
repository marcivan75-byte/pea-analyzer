from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

FEATURES = ("vol_z", "drawdown_4w", "close_vs_sma200", "atr_14_pct")
GLOBAL_PARAMS = dict(max_depth=4, learning_rate=0.05, max_iter=150, l2_regularization=2.0, random_state=42)
BASE_TARGET_KEEP = 0.25443199546163664
BIG_WIN_RETURN = 0.15


def frame(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    x["vol_z"] = pd.to_numeric(df["vol_z"], errors="coerce")
    x["drawdown_4w"] = pd.to_numeric(df["drawdown_4w"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    sma200 = pd.to_numeric(df["sma200"], errors="coerce")
    x["close_vs_sma200"] = close / sma200 - 1.0
    x["atr_14_pct"] = pd.to_numeric(df["atr_14_pct"], errors="coerce")
    x["label"] = df["hit_stop"].astype("boolean")
    x["date"] = pd.to_datetime(df["as_of_date"], errors="coerce")
    x["ret26"] = pd.to_numeric(df["forward_ret_true_26w"], errors="coerce")
    x["mae"] = pd.to_numeric(df.get("mae"), errors="coerce")
    x["mfe"] = pd.to_numeric(df.get("mfe"), errors="coerce")
    valid = (
        np.isfinite(x[list(FEATURES)]).all(axis=1)
        & x["label"].notna()
        & x["date"].notna()
        & (sma200 > 0)
        & (x["atr_14_pct"] >= 0)
    )
    out = x.loc[valid].copy()
    out["label"] = out["label"].astype(int)
    return out.sort_values("date", kind="stable")


def add_regime(x: pd.DataFrame, atr_cut: float) -> pd.DataFrame:
    out = x.copy()
    weekly = out.groupby("date", sort=False).agg(
        market_trend=("close_vs_sma200", "median"),
        market_atr=("atr_14_pct", "median"),
    )
    out = out.join(weekly, on="date")
    bull = out["market_trend"] > 0
    calm = out["market_atr"] <= atr_cut
    out["regime"] = np.select(
        [bull & calm, bull & ~calm, ~bull & calm, ~bull & ~calm],
        ["BULL_CALM", "BULL_VOLATILE", "BEAR_CALM", "BEAR_VOLATILE"],
        default="UNKNOWN",
    )
    return out


def metrics(x: pd.DataFrame, keep: np.ndarray) -> dict[str, float | int | None]:
    kept = x.loc[np.asarray(keep, dtype=bool)].copy()
    mature = kept[kept["ret26"].notna()].copy()
    if mature.empty:
        return {"kept": int(len(kept)), "mature": 0, "win_rate": None, "expectancy": None, "profit_factor": None, "payoff_ratio": None, "stop_rate": None, "big_winner_count": 0, "net_signal_pnl_units": None}
    r = mature["ret26"].astype(float)
    w = r[r > 0]
    l = r[r <= 0]
    gp = float(w.sum()) if len(w) else 0.0
    gl = float((-l).sum()) if len(l) else 0.0
    aw = float(w.mean()) if len(w) else None
    al = float(l.mean()) if len(l) else None
    return {
        "kept": int(len(kept)),
        "mature": int(len(mature)),
        "win_rate": float((r > 0).mean()),
        "expectancy": float(r.mean()),
        "profit_factor": float(gp / gl) if gl > 0 else None,
        "payoff_ratio": float(aw / abs(al)) if aw is not None and al not in (None, 0.0) else None,
        "stop_rate": float(mature["label"].mean()),
        "big_winner_count": int((r >= BIG_WIN_RETURN).sum()),
        "net_signal_pnl_units": float(r.sum()),
    }


def objective(m: dict[str, float | int | None], keep_rate: float, big_recall: float) -> float:
    if not m["mature"] or m["expectancy"] is None or m["profit_factor"] is None or m["stop_rate"] is None:
        return -1e9
    if keep_rate < 0.12 or keep_rate > 0.45:
        return -1e9
    pf = max(float(m["profit_factor"]), 1e-9)
    return (
        float(m["expectancy"])
        + 0.010 * np.log(pf)
        - 0.020 * float(m["stop_rate"])
        + 0.015 * big_recall
        - 0.010 * abs(keep_rate - BASE_TARGET_KEEP)
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()

    train = frame(pd.read_csv(args.input_dir / "V22_1_TRAIN.csv", low_memory=False))
    holdout = frame(pd.read_csv(args.input_dir / "V22_1_HOLDOUT.csv", low_memory=False))
    split = int(len(train) * 0.80)
    fit = train.iloc[:split].copy()
    valid = train.iloc[split:].copy()
    if len(fit) < 1000 or len(valid) < 1000:
        raise SystemExit("BLOCK_DATA_MAE_V3: insufficient temporal train/validation")

    atr_cut = float(fit.groupby("date")["atr_14_pct"].median().median())
    fit = add_regime(fit, atr_cut)
    valid = add_regime(valid, atr_cut)
    holdout = add_regime(holdout, atr_cut)

    model = HistGradientBoostingClassifier(**GLOBAL_PARAMS)
    model.fit(fit[list(FEATURES)], fit["label"])
    p_valid = model.predict_proba(valid[list(FEATURES)])[:, 1]
    p_hold = model.predict_proba(holdout[list(FEATURES)])[:, 1]

    global_threshold = float(np.quantile(p_valid, BASE_TARGET_KEEP))
    base_keep = p_valid <= global_threshold
    base_m = metrics(valid, base_keep)

    offsets = (-0.06, -0.03, 0.0, 0.03, 0.06)
    regimes = ("BULL_CALM", "BULL_VOLATILE", "BEAR_CALM", "BEAR_VOLATILE")
    candidates: dict[str, list[dict[str, float]]] = {}
    for regime in regimes:
        idx = valid["regime"].eq(regime).to_numpy()
        g = valid.loc[idx]
        pg = p_valid[idx]
        total_big = int((g["ret26"] >= BIG_WIN_RETURN).sum())
        rows = []
        for off in offsets:
            th = float(np.clip(global_threshold + off, 0.05, 0.95))
            keep = pg <= th
            m = metrics(g, keep)
            kr = float(keep.mean()) if len(keep) else 0.0
            kept_big = int(((g["ret26"] >= BIG_WIN_RETURN).to_numpy() & keep).sum())
            recall = float(kept_big / total_big) if total_big else 0.0
            rows.append({"threshold": th, "objective": objective(m, kr, recall), "keep_rate": kr, "big_winner_recall": recall})
        candidates[regime] = rows

    selected = {r: max(candidates[r], key=lambda z: z["objective"])["threshold"] for r in regimes}
    v3_keep_valid = np.array([p <= selected.get(r, global_threshold) for p, r in zip(p_valid, valid["regime"], strict=False)], dtype=bool)
    v3_valid_m = metrics(valid, v3_keep_valid)

    v2_keep_hold = p_hold <= global_threshold
    v3_keep_hold = np.array([p <= selected.get(r, global_threshold) for p, r in zip(p_hold, holdout["regime"], strict=False)], dtype=bool)
    v2_hold_m = metrics(holdout, v2_keep_hold)
    v3_hold_m = metrics(holdout, v3_keep_hold)

    report = {
        "version": "V22.1_MAE_V3_REGIME_RESEARCH",
        "governance": {
            "training_source": "PRE_2023_TECHNICAL_PIT_ONLY",
            "holdout_used_for_tuning": False,
            "holdout_scope": "EVALUATION_ONLY",
            "promotion_automatic": False,
            "regime_source": "CROSS_SECTIONAL_WEEKLY_MEDIAN_CLOSE_VS_SMA200_AND_ATR14_PCT",
            "regime_atr_cut_source": "FIT_PERIOD_ONLY",
            "big_winner_return_definition": BIG_WIN_RETURN,
        },
        "model": {"type": "HIST_GRADIENT_BOOSTING", "params": GLOBAL_PARAMS},
        "global_v2": {
            "threshold": global_threshold,
            "validation_auc": float(roc_auc_score(valid["label"], p_valid)),
            "validation_brier": float(brier_score_loss(valid["label"], p_valid)),
            "validation": base_m,
            "holdout_evaluation": v2_hold_m,
        },
        "adaptive_v3": {
            "regime_atr_cut": atr_cut,
            "thresholds": selected,
            "validation": v3_valid_m,
            "holdout_evaluation": v3_hold_m,
        },
        "decision_rule": "RESEARCH_ONLY; PROMOTE_ONLY_IF_PRE2023_VALIDATION_IMPROVES_AND_HOLDOUT_EVALUATION_DOES_NOT_SHOW_MATERIAL_RISK_DEGRADATION",
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "V22_1_MAE_V3_REGIME_RESEARCH.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    pd.DataFrame([
        {"regime": r, **row} for r, rows in candidates.items() for row in rows
    ]).to_csv(args.out_dir / "V22_1_MAE_V3_THRESHOLD_GRID.csv", index=False)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
