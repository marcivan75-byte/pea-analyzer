from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from v182.hebdo import backtest_v22_1 as base
from v182.hebdo import mae_predictor as mae_mod


MAX_HORIZON = max(base.HORIZON_DAYS.values())


def _numeric_array(frame: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float, copy=False)


def _row_stops(frame: pd.DataFrame, stop_pct: float, stop_policy: str) -> np.ndarray:
    if stop_policy == "fixed":
        return np.full(len(frame), float(stop_pct), dtype=float)
    if stop_policy == "atr":
        atr = pd.to_numeric(frame["atr_14_pct"], errors="coerce").to_numpy(dtype=float, copy=False)
        invalid = ~np.isfinite(atr) | (atr <= 0)
        if invalid.any():
            raise base.HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: invalid atr_14_pct for ATR stop")
        return np.clip(atr * 2.5, 0.06, 0.12)
    raise ValueError(f"unsupported stop_policy={stop_policy}")


def add_true_forward_returns_fast(
    features: pd.DataFrame,
    ohlcv: pd.DataFrame,
    *,
    stop_pct: float = 0.09,
    stop_policy: str = "fixed",
) -> pd.DataFrame:
    """Vectorized equivalent of the governed V22.1 forward ledger."""
    f, o = base._validate_inputs(features, ohlcv)
    f = f.reset_index(drop=True)
    n = len(f)

    signal_market_date = np.full(n, None, dtype=object)
    entry_date = np.full(n, None, dtype=object)
    entry_price = np.full(n, np.nan, dtype=float)
    stop_used = _row_stops(f, stop_pct, stop_policy)
    aligned = np.zeros(n, dtype=bool)

    returns = {h: np.full(n, np.nan, dtype=float) for h in base.HORIZON_DAYS}
    label_end = np.full(n, None, dtype=object)
    hit_stop = np.full(n, None, dtype=object)
    day_stop = np.full(n, None, dtype=object)
    mae = np.full(n, np.nan, dtype=float)
    mfe = np.full(n, np.nan, dtype=float)

    market = {
        str(ticker): grp.sort_values("date", kind="stable").reset_index(drop=True)
        for ticker, grp in o.groupby("ticker", sort=False)
    }
    steps = np.arange(MAX_HORIZON, dtype=np.int64)

    for ticker, positions in f.groupby(f["ticker"].astype(str), sort=False).groups.items():
        hist = market.get(str(ticker))
        if hist is None or hist.empty:
            continue

        pos = np.fromiter(positions, dtype=np.int64)
        dates = hist["date"].to_numpy(copy=False)
        asof = f.loc[pos, "as_of_date"].to_numpy(copy=False)
        signal_idx = np.searchsorted(dates, asof, side="right") - 1
        eidx = signal_idx + 1

        opens = _numeric_array(hist, "open")
        lows = _numeric_array(hist, "low")
        highs = _numeric_array(hist, "high")
        closes = _numeric_array(hist, "close")

        valid = (signal_idx >= 0) & (eidx < len(hist))
        if not valid.any():
            continue
        vp = pos[valid]
        ve = eidx[valid]
        vs = signal_idx[valid]
        valid_price = np.isfinite(opens[ve]) & (opens[ve] > 0)
        vp = vp[valid_price]
        ve = ve[valid_price]
        vs = vs[valid_price]
        if len(vp) == 0:
            continue

        aligned[vp] = True
        signal_market_date[vp] = dates[vs]
        entry_date[vp] = dates[ve]
        entry_price[vp] = opens[ve]

        raw_idx = ve[:, None] + steps[None, :]
        valid_step = raw_idx < len(hist)
        safe_idx = np.minimum(raw_idx, len(hist) - 1)
        low_window = lows[safe_idx]
        threshold = entry_price[vp, None] * (1.0 - stop_used[vp, None])
        touched = valid_step & np.isfinite(low_window) & (low_window <= threshold)

        for horizon, days in base.HORIZON_DAYS.items():
            complete = ve + days <= len(hist)
            if not complete.any():
                continue
            hp = vp[complete]
            he = ve[complete]
            stop_h = touched[complete, :days].any(axis=1)
            final_close = closes[he + days - 1]
            pnl = np.where(stop_h, -stop_used[hp], final_close / entry_price[hp] - 1.0)
            pnl[~stop_h & ~np.isfinite(final_close)] = np.nan
            returns[horizon][hp] = pnl

        complete_126 = ve + MAX_HORIZON <= len(hist)
        if complete_126.any():
            hp = vp[complete_126]
            he = ve[complete_126]
            touched_126 = touched[complete_126]
            any_stop = touched_126.any(axis=1)
            first = touched_126.argmax(axis=1)
            label_end[hp] = dates[he + MAX_HORIZON - 1]
            hit_stop[hp] = any_stop.astype(bool)
            day_stop[hp] = np.where(any_stop, first, None)

            low_126 = low_window[complete_126]
            high_126 = highs[safe_idx[complete_126]]
            finite_low = np.isfinite(low_126)
            finite_high = np.isfinite(high_126)
            min_low = np.where(finite_low, low_126, np.inf).min(axis=1)
            max_high = np.where(finite_high, high_126, -np.inf).max(axis=1)
            min_low[~finite_low.any(axis=1)] = np.nan
            max_high[~finite_high.any(axis=1)] = np.nan
            mae[hp] = min_low / entry_price[hp] - 1.0
            mfe[hp] = max_high / entry_price[hp] - 1.0

    if not aligned.any():
        raise base.HistoricalPITUnavailable("BLOCK_DATA_BACKTEST: no alignable historical observations")

    keep = np.flatnonzero(aligned)
    result = f.iloc[keep].copy().reset_index(drop=True)
    result["signal_market_date"] = pd.to_datetime(signal_market_date[keep], errors="coerce")
    result["entry_date"] = pd.to_datetime(entry_date[keep], errors="coerce")
    result["entry_price"] = entry_price[keep]
    result["execution_policy"] = "NEXT_SESSION_OPEN_J1"
    result["stop_policy"] = stop_policy
    result["stop_pct_used"] = stop_used[keep]
    for horizon in base.HORIZON_DAYS:
        result[f"forward_ret_true_{horizon}"] = returns[horizon][keep]
    result["label_end_date_26w"] = pd.to_datetime(label_end[keep], errors="coerce")
    result["hit_stop"] = pd.array(hit_stop[keep], dtype="boolean")
    result["day_stop"] = pd.array(day_stop[keep], dtype="Int64")
    result["mae"] = mae[keep]
    result["mfe"] = mfe[keep]
    return result


def _mae_feature_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    required = set(mae_mod.RAW_REQUIRED_FEATURES)
    missing = required.difference(frame.columns)
    if missing:
        raise mae_mod.MAEDataUnavailable(f"BLOCK_DATA_MAE_TRAIN: missing {sorted(missing)}")

    vol_z = pd.to_numeric(frame["vol_z"], errors="coerce")
    drawdown = pd.to_numeric(frame["drawdown_4w"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    sma200 = pd.to_numeric(frame["sma200"], errors="coerce")
    atr = pd.to_numeric(frame["atr_14_pct"], errors="coerce")
    valid = (
        vol_z.notna() & np.isfinite(vol_z)
        & drawdown.notna() & np.isfinite(drawdown)
        & close.notna() & np.isfinite(close)
        & sma200.notna() & np.isfinite(sma200) & (sma200 > 0)
        & atr.notna() & np.isfinite(atr) & (atr >= 0)
    )
    X = pd.DataFrame(
        {
            "vol_z": vol_z,
            "drawdown_4w": drawdown,
            "close_vs_sma200": close / sma200 - 1.0,
            "atr_14_pct": atr,
        },
        index=frame.index,
    )
    return X, valid


def train_stop_model_fast(
    history: pd.DataFrame,
    *,
    label_col: str = "hit_stop",
    date_col: str = "as_of_date",
) -> dict[str, object]:
    required = set(mae_mod.RAW_REQUIRED_FEATURES) | {label_col, date_col}
    missing = required.difference(history.columns)
    if missing:
        raise mae_mod.MAEDataUnavailable(f"BLOCK_DATA_MAE_TRAIN: missing {sorted(missing)}")

    work = history.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    X, feature_valid = _mae_feature_frame(work)
    labels = work[label_col].astype("boolean")
    valid = feature_valid & labels.notna() & work[date_col].notna()
    clean = X.loc[valid].copy()
    clean["label"] = labels.loc[valid].astype(int).to_numpy()
    clean["date"] = work.loc[valid, date_col].to_numpy()
    clean = clean.sort_values("date", kind="stable")

    if len(clean) < mae_mod.MIN_TRAIN_ROWS:
        raise mae_mod.MAEDataUnavailable(f"BLOCK_DATA_MAE_TRAIN: only {len(clean)} complete rows")
    if clean["label"].nunique() < 2:
        raise mae_mod.MAEDataUnavailable("BLOCK_DATA_MAE_TRAIN: label has only one class")

    split = int(len(clean) * 0.80)
    train = clean.iloc[:split]
    valid_frame = clean.iloc[split:]
    if len(valid_frame) < 30 or train["label"].nunique() < 2:
        raise mae_mod.MAEDataUnavailable("BLOCK_DATA_MAE_TRAIN: temporal validation split insufficient")

    names = list(mae_mod.REQUIRED_FEATURES)
    X_train = train[names].astype(float)
    y_train = train["label"].astype(int)
    X_valid = valid_frame[names].astype(float)
    y_valid = valid_frame["label"].astype(int)

    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression(max_iter=5000, class_weight="balanced", random_state=42)
    model.fit(scaler.transform(X_train), y_train)
    prob = model.predict_proba(scaler.transform(X_valid))[:, 1]

    auc = float(roc_auc_score(y_valid, prob)) if y_valid.nunique() == 2 else None
    brier = float(brier_score_loss(y_valid, prob))
    return {
        "version": "V22.1_LOGIT_1",
        "features": names,
        "training_mean": {f: float(v) for f, v in zip(names, scaler.mean_, strict=True)},
        "training_scale": {f: float(v) for f, v in zip(names, scaler.scale_, strict=True)},
        "coef": {f: float(v) for f, v in zip(names, model.coef_[0], strict=True)},
        "intercept": float(model.intercept_[0]),
        "threshold": mae_mod.DEFAULT_THRESHOLD,
        "n_train": int(len(train)),
        "n_validation": int(len(valid_frame)),
        "validation_auc": auc,
        "validation_brier": brier,
        "train_end": str(train["date"].max()),
        "validation_start": str(valid_frame["date"].min()),
        "validation_end": str(valid_frame["date"].max()),
    }


def apply_mae_filter_fast(
    frame: pd.DataFrame,
    threshold: float = mae_mod.DEFAULT_THRESHOLD,
    *,
    trained_artifact: Mapping[str, object] | None = None,
    require_trained: bool = False,
) -> pd.DataFrame:
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be in (0, 1)")
    if require_trained and trained_artifact is None:
        raise mae_mod.MAEDataUnavailable("BLOCK_DATA_MAE_MODEL: trained artifact required")
    if trained_artifact is None:
        return mae_mod.apply_mae_filter(frame, threshold, trained_artifact=None, require_trained=require_trained)

    names = list(trained_artifact.get("features", []))
    if names != list(mae_mod.REQUIRED_FEATURES):
        raise mae_mod.MAEDataUnavailable("BLOCK_DATA_MAE_MODEL: incompatible feature contract")

    X, valid = _mae_feature_frame(frame)
    try:
        means = trained_artifact["training_mean"]
        scales = trained_artifact["training_scale"]
        coefs = trained_artifact["coef"]
        linear = np.full(len(frame), float(trained_artifact["intercept"]), dtype=float)
        for name in names:
            mean_ = float(means[name])
            scale_ = float(scales[name])
            coef_ = float(coefs[name])
            if not np.isfinite(mean_) or not np.isfinite(scale_) or scale_ <= 0 or not np.isfinite(coef_):
                raise ValueError
            values = pd.to_numeric(X[name], errors="coerce").to_numpy(dtype=float, copy=False)
            linear += coef_ * ((values - mean_) / scale_)
    except (KeyError, TypeError, ValueError) as exc:
        raise mae_mod.MAEDataUnavailable("BLOCK_DATA_MAE_MODEL: invalid trained artifact") from exc

    probability = np.full(len(frame), np.nan, dtype=float)
    idx = valid.to_numpy(dtype=bool, copy=False)
    probability[idx] = 1.0 / (1.0 + np.exp(-linear[idx]))
    status = np.full(len(frame), "BLOCK_DATA_MAE", dtype=object)
    status[idx] = np.where(probability[idx] > threshold, "EXCLU_MAE", "OK")

    out = frame.copy()
    out["stop_prob"] = probability
    out["mae_status"] = status
    out["EXCLU_MAE"] = out["mae_status"].eq("EXCLU_MAE")
    out["mae_model_type"] = "TRAINED"
    return out


def main() -> int:
    base.add_true_forward_returns = add_true_forward_returns_fast
    base.train_stop_model = train_stop_model_fast
    base.apply_mae_filter = apply_mae_filter_fast
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
