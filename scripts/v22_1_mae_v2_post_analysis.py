from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

FEATURES = ("vol_z", "drawdown_4w", "close_vs_sma200", "atr_14_pct")
BASELINE_THRESHOLD = 0.45


def _frame(df: pd.DataFrame) -> pd.DataFrame:
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


def _metrics(frame: pd.DataFrame, keep: np.ndarray) -> dict[str, float | int | None]:
    kept = frame.loc[keep].copy()
    mature = kept[kept["ret26"].notna()].copy()
    if mature.empty:
        return {"kept": int(len(kept)), "mature": 0, "win_rate": None, "expectancy": None, "profit_factor": None, "stop_rate": None}
    ret = mature["ret26"].astype(float)
    wins = ret[ret > 0]
    losses = ret[ret <= 0]
    gross_loss = float((-losses).sum())
    return {
        "kept": int(len(kept)),
        "mature": int(len(mature)),
        "win_rate": float((ret > 0).mean()),
        "expectancy": float(ret.mean()),
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else None,
        "stop_rate": float(mature["label"].mean()),
    }


def _quarter_status(holdout: pd.DataFrame, analysis_end: pd.Timestamp) -> pd.DataFrame:
    x = holdout.copy()
    x["as_of_date"] = pd.to_datetime(x["as_of_date"], errors="coerce")
    x["ret26"] = pd.to_numeric(x["forward_ret_true_26w"], errors="coerce")
    x = x.dropna(subset=["as_of_date"])
    x["period"] = x["as_of_date"].dt.to_period("Q")
    rows = []
    for period, g in x.groupby("period", sort=True):
        total = int(len(g))
        mature = int(g["ret26"].notna().sum())
        calendar_complete = bool(period.end_time.normalize() <= analysis_end.normalize())
        outcome_complete = bool(total > 0 and mature == total)
        if calendar_complete and outcome_complete:
            status = "COMPLETE"
        elif calendar_complete:
            status = "CALENDAR_COMPLETE_OUTCOME_INCOMPLETE"
        else:
            status = "CALENDAR_INCOMPLETE"
        rows.append({
            "period": str(period),
            "signal_rows": total,
            "completed_26w": mature,
            "maturity_ratio": float(mature / total) if total else None,
            "calendar_complete": calendar_complete,
            "outcome_complete_26w": outcome_complete,
            "status": status,
        })
    return pd.DataFrame(rows)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--analysis-end", default="2026-08-31")
    args = p.parse_args()

    train_raw = pd.read_csv(args.input_dir / "V22_1_TRAIN.csv", low_memory=False)
    holdout_raw = pd.read_csv(args.input_dir / "V22_1_HOLDOUT.csv", low_memory=False)
    train = _frame(train_raw)
    holdout = _frame(holdout_raw)
    if len(train) < 1000:
        raise SystemExit("BLOCK_DATA_MAE_V2: insufficient pre-2023 training observations")

    split = int(len(train) * 0.80)
    fit = train.iloc[:split].copy()
    valid = train.iloc[split:].copy()
    if valid.empty or valid["label"].nunique() < 2:
        raise SystemExit("BLOCK_DATA_MAE_V2: temporal validation split invalid")

    scaler = StandardScaler().fit(fit[list(FEATURES)])
    baseline = LogisticRegression(max_iter=5000, class_weight="balanced", random_state=42)
    baseline.fit(scaler.transform(fit[list(FEATURES)]), fit["label"])
    p_base = baseline.predict_proba(scaler.transform(valid[list(FEATURES)]))[:, 1]
    base_auc = float(roc_auc_score(valid["label"], p_base))
    base_brier = float(brier_score_loss(valid["label"], p_base))
    target_keep = float((p_base <= BASELINE_THRESHOLD).mean())

    candidate = HistGradientBoostingClassifier(
        max_depth=4,
        learning_rate=0.05,
        max_iter=150,
        l2_regularization=2.0,
        random_state=42,
    )
    candidate.fit(fit[list(FEATURES)], fit["label"])
    p_valid = candidate.predict_proba(valid[list(FEATURES)])[:, 1]
    cand_auc = float(roc_auc_score(valid["label"], p_valid))
    cand_brier = float(brier_score_loss(valid["label"], p_valid))
    threshold = float(np.quantile(p_valid, target_keep))

    validation_baseline = _metrics(valid, p_base <= BASELINE_THRESHOLD)
    validation_candidate = _metrics(valid, p_valid <= threshold)

    p_holdout = candidate.predict_proba(holdout[list(FEATURES)])[:, 1]
    holdout_candidate = _metrics(holdout, p_holdout <= threshold)

    candidate_authorized_for_research = bool(cand_auc > base_auc and cand_brier < base_brier)
    report = {
        "version": "V22.1_MAE_V2_RESEARCH",
        "governance": {
            "features": list(FEATURES),
            "training_source": "PRE_2023_TECHNICAL_PIT_ONLY",
            "temporal_split": "FIRST_80_FIT_LAST_20_VALIDATION",
            "holdout_used_for_tuning": False,
            "holdout_scope": "EVALUATION_ONLY",
            "promotion_automatic": False,
        },
        "baseline": {
            "model": "LOGISTIC_REGRESSION",
            "threshold": BASELINE_THRESHOLD,
            "validation_auc": base_auc,
            "validation_brier": base_brier,
            "validation": validation_baseline,
        },
        "candidate": {
            "model": "HIST_GRADIENT_BOOSTING",
            "fixed_params": {"max_depth": 4, "learning_rate": 0.05, "max_iter": 150, "l2_regularization": 2.0},
            "threshold_source": "PRE_2023_VALIDATION_MATCH_BASELINE_KEEP_RATE",
            "threshold": threshold,
            "target_keep_rate": target_keep,
            "validation_auc": cand_auc,
            "validation_brier": cand_brier,
            "validation": validation_candidate,
            "holdout_evaluation": holdout_candidate,
            "research_authorized": candidate_authorized_for_research,
        },
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "V22_1_MAE_V2_RESEARCH.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    _quarter_status(holdout_raw, pd.Timestamp(args.analysis_end)).to_csv(args.out_dir / "V22_1_QUARTER_COMPLETENESS.csv", index=False)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
