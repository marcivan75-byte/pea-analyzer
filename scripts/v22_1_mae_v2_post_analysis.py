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


def _metrics(frame: pd.DataFrame, keep: np.ndarray) -> dict[str, float | int | None]:
    kept = frame.loc[keep].copy()
    mature = kept[kept["ret26"].notna()].copy()
    if mature.empty:
        return {
            "kept": int(len(kept)), "mature": 0, "wins": 0, "win_rate": None,
            "losses": 0, "loss_rate": None, "false_positives": 0,
            "false_positive_rate": None, "avg_win": None, "avg_loss": None,
            "expectancy": None, "profit_factor": None, "payoff_ratio": None,
            "stop_count": 0, "stop_rate": None, "mae_mean": None, "mfe_mean": None,
        }
    ret = mature["ret26"].astype(float)
    wins = ret[ret > 0]
    losses = ret[ret <= 0]
    stop = mature["label"].astype(bool)
    gross_loss = float((-losses).sum())
    avg_win = float(wins.mean()) if not wins.empty else None
    avg_loss = float(losses.mean()) if not losses.empty else None
    return {
        "kept": int(len(kept)),
        "mature": int(len(mature)),
        "wins": int((ret > 0).sum()),
        "win_rate": float((ret > 0).mean()),
        "losses": int((ret <= 0).sum()),
        "loss_rate": float((ret <= 0).mean()),
        "false_positives": int(stop.sum()),
        "false_positive_rate": float(stop.mean()),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": float(ret.mean()),
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else None,
        "payoff_ratio": (avg_win / abs(avg_loss)) if avg_win is not None and avg_loss not in (None, 0.0) else None,
        "stop_count": int(stop.sum()),
        "stop_rate": float(stop.mean()),
        "mae_mean": float(pd.to_numeric(mature["mae"], errors="coerce").mean()),
        "mfe_mean": float(pd.to_numeric(mature["mfe"], errors="coerce").mean()),
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


def _period_comparison(
    frame: pd.DataFrame,
    base_keep: np.ndarray,
    v2_keep: np.ndarray,
    completeness: pd.DataFrame,
    period_kind: str,
) -> pd.DataFrame:
    work = frame.copy()
    work["base_keep"] = np.asarray(base_keep, dtype=bool)
    work["v2_keep"] = np.asarray(v2_keep, dtype=bool)
    if period_kind == "quarter":
        work["period"] = work["date"].dt.to_period("Q").astype(str)
        complete_map = completeness.set_index("period")["status"].to_dict()
    elif period_kind == "year":
        work["period"] = work["date"].dt.year.astype(str)
        complete_map = {}
    else:
        raise ValueError(period_kind)

    rows: list[dict[str, object]] = []
    for period, g in work.groupby("period", sort=True):
        if period_kind == "quarter":
            status = complete_map.get(str(period), "UNKNOWN")
            publish_final = status == "COMPLETE"
        else:
            quarters = completeness[completeness["period"].str.startswith(str(period))]
            publish_final = bool(len(quarters) == 4 and quarters["status"].eq("COMPLETE").all())
            status = "COMPLETE" if publish_final else "OUTCOME_INCOMPLETE"

        masks = {
            "FULL": np.ones(len(g), dtype=bool),
            "MAE_V1": g["base_keep"].to_numpy(dtype=bool),
            "MAE_V2": g["v2_keep"].to_numpy(dtype=bool),
        }
        for variant, mask in masks.items():
            m = _metrics(g, mask)
            row: dict[str, object] = {
                "period": str(period),
                "variant": variant,
                "status": status,
                "final_metrics_publishable": publish_final,
                **m,
            }
            if not publish_final:
                for key in (
                    "wins", "win_rate", "losses", "loss_rate", "false_positives",
                    "false_positive_rate", "avg_win", "avg_loss", "expectancy",
                    "profit_factor", "payoff_ratio", "stop_count", "stop_rate",
                    "mae_mean", "mfe_mean",
                ):
                    row[key] = None
            rows.append(row)
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

    p_holdout_base = baseline.predict_proba(scaler.transform(holdout[list(FEATURES)]))[:, 1]
    p_holdout_v2 = candidate.predict_proba(holdout[list(FEATURES)])[:, 1]
    base_keep_holdout = p_holdout_base <= BASELINE_THRESHOLD
    v2_keep_holdout = p_holdout_v2 <= threshold
    holdout_baseline = _metrics(holdout, base_keep_holdout)
    holdout_candidate = _metrics(holdout, v2_keep_holdout)

    candidate_authorized_for_research = bool(cand_auc > base_auc and cand_brier < base_brier)
    report = {
        "version": "V22.1_MAE_V2_RESEARCH",
        "false_positive_definition": "RETAINED_SIGNAL_THAT_HITS_STOP_WITHIN_26W",
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
            "holdout_evaluation": holdout_baseline,
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
    completeness = _quarter_status(holdout_raw, pd.Timestamp(args.analysis_end))
    quarterly = _period_comparison(holdout, base_keep_holdout, v2_keep_holdout, completeness, "quarter")
    annual = _period_comparison(holdout, base_keep_holdout, v2_keep_holdout, completeness, "year")

    (args.out_dir / "V22_1_MAE_V2_RESEARCH.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    completeness.to_csv(args.out_dir / "V22_1_QUARTER_COMPLETENESS.csv", index=False)
    quarterly.to_csv(args.out_dir / "V22_1_QUARTERLY_DETAILED_COMPARISON.csv", index=False)
    annual.to_csv(args.out_dir / "V22_1_ANNUAL_DETAILED_COMPARISON.csv", index=False)
    print(json.dumps(report, indent=2, sort_keys=True))
    print("QUARTERLY_DETAILED_ROWS", len(quarterly))
    print("ANNUAL_DETAILED_ROWS", len(annual))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
