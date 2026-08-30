from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from v182.backtest.v21_8_1_backtest_B_v2 import compute_mae_mfe, compute_true_26w_pnl
from v182.hebdo.mae_predictor import apply_mae_filter, train_stop_model
from v182.scoring.ic_lasso_selector import (
    build_governed_weights,
    compute_information_coefficient,
    lasso_select_features,
)


HORIZON_DAYS = {"1w": 5, "2w": 10, "4w": 20, "13w": 63, "26w": 126}
DEFAULT_FEATURE_COLUMNS = (
    "vol_z",
    "mom_26w_sector",
    "rsi_14_hebdo",
    "drawdown_4w",
    "atr_14_pct",
)


class HistoricalPITUnavailable(RuntimeError):
    pass


def _validate_inputs(features: pd.DataFrame, ohlcv: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required_features = {"ticker", "as_of_date"}
    required_ohlcv = {"ticker", "date", "low", "high", "close"}
    miss_f = required_features.difference(features.columns)
    miss_o = required_ohlcv.difference(ohlcv.columns)
    if miss_f:
        raise HistoricalPITUnavailable(f"BLOCK_DATA_BACKTEST: feature columns missing {sorted(miss_f)}")
    if miss_o:
        raise HistoricalPITUnavailable(f"BLOCK_DATA_BACKTEST: OHLCV columns missing {sorted(miss_o)}")

    f = features.copy()
    o = ohlcv.copy()
    f["as_of_date"] = pd.to_datetime(f["as_of_date"], errors="coerce")
    o["date"] = pd.to_datetime(o["date"], errors="coerce")
    f = f.dropna(subset=["ticker", "as_of_date"])
    o = o.dropna(subset=["ticker", "date"]).sort_values(["ticker", "date"])
    if f.empty or o.empty:
        raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: empty historical PIT features or OHLCV")

    ts_col = "pit_observed_at" if "pit_observed_at" in f.columns else "_pit_observed_at_utc" if "_pit_observed_at_utc" in f.columns else None
    if ts_col is None:
        raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: historical features lack PIT observation timestamp")
    observed = pd.to_datetime(f[ts_col], errors="coerce", utc=True)
    asof_utc = pd.to_datetime(f["as_of_date"], errors="coerce", utc=True)
    if observed.isna().any() or bool((observed > asof_utc).any()):
        raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: future/invalid PIT feature timestamp")
    return f, o


def add_true_forward_returns(
    features: pd.DataFrame,
    ohlcv: pd.DataFrame,
    *,
    stop_pct: float = 0.09,
) -> pd.DataFrame:
    f, o = _validate_inputs(features, ohlcv)
    output: list[dict[str, object]] = []
    grouped = {str(t): g.set_index("date").sort_index() for t, g in o.groupby("ticker", sort=False)}

    for _, row in f.iterrows():
        ticker = str(row["ticker"])
        hist = grouped.get(ticker)
        if hist is None or hist.empty:
            continue
        dates = hist.index[hist.index <= row["as_of_date"]]
        if len(dates) == 0:
            continue
        entry_date = dates[-1]
        loc = hist.index.get_loc(entry_date)
        if not isinstance(loc, (int, np.integer)):
            continue
        entry_close = pd.to_numeric(pd.Series([hist.iloc[int(loc)]["close"]]), errors="coerce").iloc[0]
        if not np.isfinite(entry_close) or entry_close <= 0:
            continue

        record = row.to_dict()
        record["entry_date"] = entry_date
        record["entry_price"] = float(entry_close)
        enough_26w = int(loc) + 126 < len(hist)
        for horizon, days in HORIZON_DAYS.items():
            if int(loc) + days >= len(hist):
                record[f"forward_ret_true_{horizon}"] = np.nan
                continue
            forward = hist.iloc[int(loc) + 1 : int(loc) + 1 + days]
            pnl, _, _, _ = compute_true_26w_pnl(float(entry_close), forward, stop_pct=stop_pct)
            record[f"forward_ret_true_{horizon}"] = pnl
        if enough_26w:
            full = hist.iloc[int(loc) + 1 : int(loc) + 1 + 126]
            pnl26, hit, day_stop, _ = compute_true_26w_pnl(float(entry_close), full, stop_pct=stop_pct)
            mae, mfe = compute_mae_mfe(float(entry_close), full)
            record["forward_ret_true_26w"] = pnl26
            record["hit_stop"] = bool(hit)
            record["day_stop"] = day_stop
            record["mae"] = mae
            record["mfe"] = mfe
        else:
            record["hit_stop"] = pd.NA
            record["day_stop"] = pd.NA
            record["mae"] = np.nan
            record["mfe"] = np.nan
        output.append(record)

    result = pd.DataFrame(output)
    if result.empty:
        raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: no alignable historical observations")
    return result


def train_governed_model(
    ledger: pd.DataFrame,
    feature_columns: tuple[str, ...] = DEFAULT_FEATURE_COLUMNS,
) -> tuple[pd.DataFrame, dict[str, dict[str, float | str]], dict[str, object]]:
    missing = sorted(set(feature_columns).difference(ledger.columns))
    if missing:
        raise HistoricalPITUnavailable(f"BLOCK_DATA_BACKTEST: model features missing {missing}")
    y = pd.to_numeric(ledger["forward_ret_true_26w"], errors="coerce")
    X = ledger.loc[:, feature_columns].apply(pd.to_numeric, errors="coerce")
    usable = pd.concat([X, y.rename("y")], axis=1).dropna()
    if len(usable) < 100:
        raise HistoricalPITUnavailable(f"BLOCK_DATA_BACKTEST: only {len(usable)} complete rows for Lasso")

    ic = compute_information_coefficient(usable[list(feature_columns)], usable["y"])
    selected, alpha, _ = lasso_select_features(usable[list(feature_columns)], usable["y"])
    weights = build_governed_weights(selected)
    if not weights:
        raise HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: Lasso selected no governed features")
    meta = {"n_complete": int(len(usable)), "lasso_alpha": alpha, "features": list(feature_columns)}
    return ic, weights, meta


def _score_with_frozen_weights(frame: pd.DataFrame, weights: dict[str, dict[str, object]]) -> pd.Series:
    score = pd.Series(0.0, index=frame.index, dtype=float)
    valid = pd.Series(True, index=frame.index)
    for feature, spec in weights.items():
        if feature not in frame.columns:
            valid[:] = False
            continue
        values = pd.to_numeric(frame[feature], errors="coerce")
        try:
            mean_ = float(spec["training_mean"])
            scale_ = float(spec["training_scale"])
            weight = float(spec["weight"])
        except (KeyError, TypeError, ValueError):
            valid[:] = False
            continue
        observed = values.notna() & np.isfinite(values) & np.isfinite(mean_) & np.isfinite(scale_) & (scale_ > 0)
        valid &= observed
        direction = -1.0 if str(spec.get("direction", "LONG")).upper() == "SHORT" else 1.0
        score = score + direction * weight * ((values - mean_) / scale_)
    score.loc[~valid] = np.nan
    return score


def _oos_ic(frame: pd.DataFrame, score_col: str = "governed_score") -> dict[str, float | int | None]:
    out: dict[str, float | int | None] = {}
    score = pd.to_numeric(frame[score_col], errors="coerce")
    for horizon in ("1w", "4w", "26w"):
        ret = pd.to_numeric(frame.get(f"forward_ret_true_{horizon}"), errors="coerce")
        valid = score.notna() & ret.notna()
        n = int(valid.sum())
        out[f"ic_{horizon}"] = float(spearmanr(score[valid], ret[valid]).statistic) if n >= 30 else None
        out[f"ic_{horizon}_n"] = n
    return out


def acceptance_metrics(ledger: pd.DataFrame) -> dict[str, float | int | None]:
    ret26 = pd.to_numeric(ledger.get("forward_ret_true_26w"), errors="coerce").dropna()
    mae = pd.to_numeric(ledger.get("mae"), errors="coerce").dropna()
    stops = ledger.get("hit_stop")
    stop_rate = None
    if stops is not None:
        s = stops.dropna().astype(bool)
        stop_rate = float(s.mean()) if not s.empty else None
    return {
        "rows": int(len(ledger)),
        "hit_rate_26w_true": float((ret26 > 0).mean()) if not ret26.empty else None,
        "expectancy_26w_true": float(ret26.mean()) if not ret26.empty else None,
        "mae_mean": float(mae.mean()) if not mae.empty else None,
        "stop_rate": stop_rate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/hebdo/backtest_v22_1"))
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--holdout-start", default="2024-01-01")
    args = parser.parse_args()
    if not args.features.is_file() or not args.ohlcv.is_file():
        raise SystemExit("BLOCK_DATA_BACKTEST: required PIT historical files missing")

    features = pd.read_csv(args.features)
    ohlcv = pd.read_csv(args.ohlcv)
    if "as_of_date" in features.columns:
        dates = pd.to_datetime(features["as_of_date"], errors="coerce")
        features = features[(dates >= pd.Timestamp(args.start)) & (dates <= pd.Timestamp(args.end))].copy()
    try:
        ledger = add_true_forward_returns(features, ohlcv)
        ledger["as_of_date"] = pd.to_datetime(ledger["as_of_date"], errors="coerce")
        train = ledger[ledger["as_of_date"] < pd.Timestamp(args.holdout_start)].copy()
        holdout = ledger[ledger["as_of_date"] >= pd.Timestamp(args.holdout_start)].copy()
        if len(train) < 150 or len(holdout) < 30:
            raise HistoricalPITUnavailable(
                f"BLOCK_DATA_BACKTEST: temporal split insufficient train={len(train)} holdout={len(holdout)}"
            )
        ic_train, weights, model_meta = train_governed_model(train)
        mae_model = train_stop_model(train)
    except (HistoricalPITUnavailable, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    holdout["governed_score"] = _score_with_frozen_weights(holdout, weights)
    holdout = apply_mae_filter(holdout, trained_artifact=mae_model, require_trained=True)
    retained = holdout[holdout["mae_status"].eq("OK")].copy()

    report = {
        "period": [args.start, args.end],
        "holdout_start": args.holdout_start,
        "train_rows": int(len(train)),
        "holdout_rows": int(len(holdout)),
        "model": model_meta,
        "mae_model": {
            "n_train": mae_model["n_train"],
            "n_validation": mae_model["n_validation"],
            "validation_auc": mae_model["validation_auc"],
            "validation_brier": mae_model["validation_brier"],
        },
        "holdout_all": {**acceptance_metrics(holdout), **_oos_ic(holdout)},
        "holdout_after_mae_filter": {**acceptance_metrics(retained), **_oos_ic(retained)},
    }

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(out / "V22_1_TRUE_FORWARD_LEDGER.csv", index=False)
    train.to_csv(out / "V22_1_TRAIN_LEDGER.csv", index=False)
    holdout.to_csv(out / "V22_1_HOLDOUT_LEDGER.csv", index=False)
    ic_train.to_csv(out / "V22_1_IC_TRAIN_26W.csv", index=False)
    (out / "V22_1_GOVERNED_WEIGHTS.json").write_text(json.dumps(weights, indent=2), encoding="utf-8")
    (out / "V22_1_MAE_MODEL.json").write_text(json.dumps(mae_model, indent=2), encoding="utf-8")
    (out / "V22_1_BACKTEST_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
