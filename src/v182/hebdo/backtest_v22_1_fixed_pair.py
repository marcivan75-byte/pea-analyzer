from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from v182.hebdo.backtest_v22_1 import (
    HistoricalPITUnavailable,
    _read_frame,
    _score_with_frozen_weights,
    acceptance_metrics,
    add_true_forward_returns,
    train_governed_model,
)
from v182.hebdo.mae_predictor import apply_mae_filter, train_stop_model


FIXED_NO_ATR_FEATURE_COLUMNS = (
    "vol_z",
    "mom_26w",
    "rsi_14_hebdo",
    "drawdown_4w",
)


def _safe_float(value):
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def _ic(frame: pd.DataFrame, horizon: str) -> tuple[float | None, int]:
    score = pd.to_numeric(frame.get("governed_score"), errors="coerce")
    ret = pd.to_numeric(frame.get(f"forward_ret_true_{horizon}"), errors="coerce")
    valid = score.notna() & ret.notna()
    n = int(valid.sum())
    if n < 30:
        return None, n
    return _safe_float(spearmanr(score[valid], ret[valid]).statistic), n


def _quarter_metrics(frame: pd.DataFrame) -> dict[str, object]:
    ret26 = pd.to_numeric(frame.get("forward_ret_true_26w"), errors="coerce")
    ret13 = pd.to_numeric(frame.get("forward_ret_true_13w"), errors="coerce")
    ret4 = pd.to_numeric(frame.get("forward_ret_true_4w"), errors="coerce")
    ret1 = pd.to_numeric(frame.get("forward_ret_true_1w"), errors="coerce")
    mae = pd.to_numeric(frame.get("mae"), errors="coerce")
    mfe = pd.to_numeric(frame.get("mfe"), errors="coerce")
    stop = frame.get("hit_stop")
    stop_rate = None
    stop_n = 0
    if stop is not None:
        s = stop.dropna().astype(bool)
        stop_n = int(len(s))
        stop_rate = _safe_float(s.mean()) if len(s) else None

    ic1, ic1_n = _ic(frame, "1w")
    ic4, ic4_n = _ic(frame, "4w")
    ic26, ic26_n = _ic(frame, "26w")

    valid26 = ret26.dropna()
    return {
        "rows": int(len(frame)),
        "ret26_n": int(valid26.size),
        "hit_rate_26w": _safe_float((valid26 > 0).mean()) if len(valid26) else None,
        "expectancy_26w": _safe_float(valid26.mean()) if len(valid26) else None,
        "median_26w": _safe_float(valid26.median()) if len(valid26) else None,
        "std_26w": _safe_float(valid26.std(ddof=1)) if len(valid26) > 1 else None,
        "p10_26w": _safe_float(valid26.quantile(0.10)) if len(valid26) else None,
        "p25_26w": _safe_float(valid26.quantile(0.25)) if len(valid26) else None,
        "p75_26w": _safe_float(valid26.quantile(0.75)) if len(valid26) else None,
        "p90_26w": _safe_float(valid26.quantile(0.90)) if len(valid26) else None,
        "expectancy_13w": _safe_float(ret13.mean()),
        "expectancy_4w": _safe_float(ret4.mean()),
        "expectancy_1w": _safe_float(ret1.mean()),
        "mae_mean": _safe_float(mae.mean()),
        "mae_median": _safe_float(mae.median()),
        "mfe_mean": _safe_float(mfe.mean()),
        "mfe_median": _safe_float(mfe.median()),
        "stop_rate": stop_rate,
        "stop_n": stop_n,
        "ic_1w": ic1,
        "ic_1w_n": ic1_n,
        "ic_4w": ic4,
        "ic_4w_n": ic4_n,
        "ic_26w": ic26,
        "ic_26w_n": ic26_n,
        "mean_score": _safe_float(pd.to_numeric(frame.get("governed_score"), errors="coerce").mean()),
        "mean_adv20_eur": _safe_float(pd.to_numeric(frame.get("adv_20_eur"), errors="coerce").mean()),
    }


def _quarterly(frame: pd.DataFrame, phase: str, universe: str) -> pd.DataFrame:
    x = frame.copy()
    x["as_of_date"] = pd.to_datetime(x["as_of_date"], errors="coerce")
    x = x.dropna(subset=["as_of_date"])
    x["quarter"] = x["as_of_date"].dt.to_period("Q").astype(str)
    rows = []
    for quarter, grp in x.groupby("quarter", sort=True):
        row = {"universe": universe, "phase": phase, "quarter": quarter}
        row.update(_quarter_metrics(grp))
        rows.append(row)
    return pd.DataFrame(rows)


def _write_variant(
    ledger: pd.DataFrame,
    *,
    out_dir: Path,
    universe: str,
    holdout_start: str,
    start: str,
    end: str,
) -> dict[str, object]:
    ledger = ledger.copy()
    ledger["as_of_date"] = pd.to_datetime(ledger["as_of_date"], errors="coerce")
    ledger["label_end_date_26w"] = pd.to_datetime(ledger["label_end_date_26w"], errors="coerce")
    holdout_ts = pd.Timestamp(holdout_start)
    train = ledger[
        ledger["label_end_date_26w"].notna()
        & (ledger["label_end_date_26w"] < holdout_ts)
    ].copy()
    embargo = ledger[
        (ledger["as_of_date"] < holdout_ts)
        & ledger["label_end_date_26w"].notna()
        & (ledger["label_end_date_26w"] >= holdout_ts)
    ].copy()
    holdout = ledger[ledger["as_of_date"] >= holdout_ts].copy()
    if len(train) < 150 or len(holdout) < 30:
        raise HistoricalPITUnavailable(
            f"BLOCK_DATA_BACKTEST: temporal split insufficient train={len(train)} holdout={len(holdout)}"
        )
    if not embargo.empty and bool((train["label_end_date_26w"] >= holdout_ts).any()):
        raise HistoricalPITUnavailable("BLOCK_LOOKAHEAD_BACKTEST: training label crosses holdout boundary")

    ic_train, weights, model_meta = train_governed_model(train, feature_columns=FIXED_NO_ATR_FEATURE_COLUMNS)
    mae_model = train_stop_model(train)

    train["governed_score"] = _score_with_frozen_weights(train, weights)
    holdout["governed_score"] = _score_with_frozen_weights(holdout, weights)
    train = apply_mae_filter(train, trained_artifact=mae_model, require_trained=True)
    holdout = apply_mae_filter(holdout, trained_artifact=mae_model, require_trained=True)
    train_ok = train[train["mae_status"].eq("OK")].copy()
    holdout_ok = holdout[holdout["mae_status"].eq("OK")].copy()

    def oos_ic(frame: pd.DataFrame) -> dict[str, object]:
        out = {}
        for horizon in ("1w", "4w", "26w"):
            val, n = _ic(frame, horizon)
            out[f"ic_{horizon}"] = val
            out[f"ic_{horizon}_n"] = n
        return out

    report = {
        "period": [start, end],
        "holdout_start": holdout_start,
        "feature_set": "technical-core-no-atr",
        "feature_columns": list(FIXED_NO_ATR_FEATURE_COLUMNS),
        "stop_policy": "fixed",
        "fixed_stop_pct": 0.09,
        "atr_removed_from_stop_and_ranking": True,
        "execution_policy": "NEXT_SESSION_OPEN_J1",
        "label_embargo": "EXACT_26W_LABEL_END_BEFORE_HOLDOUT",
        "embargo_rows": int(len(embargo)),
        "universe": universe,
        "full_enhanced_pit_claimed": False,
        "train_rows": int(len(train)),
        "holdout_rows": int(len(holdout)),
        "model": model_meta,
        "mae_model": {
            "n_train": mae_model["n_train"],
            "n_validation": mae_model["n_validation"],
            "validation_auc": mae_model["validation_auc"],
            "validation_brier": mae_model["validation_brier"],
        },
        "holdout_all": {**acceptance_metrics(holdout), **oos_ic(holdout)},
        "holdout_after_mae_filter": {**acceptance_metrics(holdout_ok), **oos_ic(holdout_ok)},
    }

    q = pd.concat(
        [
            _quarterly(train, "TRAIN_IS_ALL", universe),
            _quarterly(train_ok, "TRAIN_IS_AFTER_MAE", universe),
            _quarterly(holdout, "HOLDOUT_OOS_ALL", universe),
            _quarterly(holdout_ok, "HOLDOUT_OOS_AFTER_MAE", universe),
        ],
        ignore_index=True,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(out_dir / "V22_1_TRUE_FORWARD_LEDGER.csv", index=False)
    train.to_csv(out_dir / "V22_1_TRAIN_LEDGER.csv", index=False)
    embargo.to_csv(out_dir / "V22_1_EMBARGO_LEDGER.csv", index=False)
    holdout.to_csv(out_dir / "V22_1_HOLDOUT_LEDGER.csv", index=False)
    ic_train.to_csv(out_dir / "V22_1_IC_TRAIN_26W.csv", index=False)
    q.to_csv(out_dir / "V22_1_QUARTERLY_STATS.csv", index=False)
    (out_dir / "V22_1_QUARTERLY_STATS.json").write_text(q.to_json(orient="records", indent=2), encoding="utf-8")
    (out_dir / "V22_1_GOVERNED_WEIGHTS.json").write_text(json.dumps(weights, indent=2), encoding="utf-8")
    (out_dir / "V22_1_MAE_MODEL.json").write_text(json.dumps(mae_model, indent=2), encoding="utf-8")
    (out_dir / "V22_1_BACKTEST_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, default=Path("outputs/hebdo/backtest_v22_1"))
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--holdout-start", default="2024-01-01")
    parser.add_argument("--min-adv-eur", type=float, default=800000.0)
    args = parser.parse_args()

    if not args.features.is_file() or not args.ohlcv.is_file():
        raise SystemExit("BLOCK_DATA_BACKTEST: required PIT historical files missing")

    features = _read_frame(args.features)
    ohlcv = _read_frame(args.ohlcv)
    dates = pd.to_datetime(features["as_of_date"], errors="coerce")
    features = features[(dates >= pd.Timestamp(args.start)) & (dates <= pd.Timestamp(args.end))].copy()

    ledger = add_true_forward_returns(features, ohlcv, stop_policy="fixed")

    full_report = _write_variant(
        ledger,
        out_dir=args.out_root / "fixed",
        universe="FULL_TECHNICAL",
        holdout_start=args.holdout_start,
        start=args.start,
        end=args.end,
    )

    adv = pd.to_numeric(ledger.get("adv_20_eur"), errors="coerce")
    liquid = ledger[adv.ge(args.min_adv_eur)].copy()
    if liquid.empty:
        raise SystemExit("BLOCK_DATA_BACKTEST: no rows pass governed ADV20 liquidity threshold")
    liquid_report = _write_variant(
        liquid,
        out_dir=args.out_root / "fixed_liquid",
        universe="ADV20_LIQUID",
        holdout_start=args.holdout_start,
        start=args.start,
        end=args.end,
    )

    comparison = {
        "runtime_design": "ONE_FIXED_FORWARD_LEDGER_REUSED_FOR_FULL_AND_LIQUID",
        "atr_removed_from_stop_and_ranking": True,
        "label_embargo": "EXACT_26W_LABEL_END_BEFORE_HOLDOUT",
        "min_adv_eur": args.min_adv_eur,
        "full": full_report,
        "liquid": liquid_report,
    }
    (args.out_root / "V22_1_FIXED_PAIR_COMPARISON.json").write_text(
        json.dumps(comparison, indent=2), encoding="utf-8"
    )
    print(json.dumps(comparison, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
