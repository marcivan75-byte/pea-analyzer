from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd


REQUIRED_FEATURES = ("vol_z", "drawdown_4w", "close", "sma200", "atr_14_pct")
DEFAULT_THRESHOLD = 0.45


class MAEDataUnavailable(ValueError):
    """Raised when stop-risk inputs cannot be proven without imputation."""


def _as_finite_float(row: Mapping[str, object] | pd.Series, key: str) -> float:
    value = row.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MAEDataUnavailable(f"BLOCK_DATA_MAE: missing/invalid {key}") from exc
    if not np.isfinite(number):
        raise MAEDataUnavailable(f"BLOCK_DATA_MAE: non-finite {key}")
    return number


def predict_stop_prob(row: Mapping[str, object] | pd.Series) -> float:
    """Deterministic V22.1 stop-risk score from the forensic rule set.

    Formula requested by the audit:
      +2.0 if vol_z > 4
      +1.5 if drawdown_4w < -12%
      +1.0 if close < sma200
      +10 * atr_14_pct
      sigmoid(score - 3)

    This is a governed heuristic, not a statistically calibrated probability model.
    Missing inputs fail closed; no ATR or fundamental value is imputed.
    """
    values = {key: _as_finite_float(row, key) for key in REQUIRED_FEATURES}
    if values["atr_14_pct"] < 0:
        raise MAEDataUnavailable("BLOCK_DATA_MAE: negative atr_14_pct")

    score = 0.0
    score += 2.0 if values["vol_z"] > 4.0 else 0.0
    score += 1.5 if values["drawdown_4w"] < -0.12 else 0.0
    score += 1.0 if values["close"] < values["sma200"] else 0.0
    score += values["atr_14_pct"] * 10.0
    return 1.0 / (1.0 + math.exp(-(score - 3.0)))


def apply_mae_filter(frame: pd.DataFrame, threshold: float = DEFAULT_THRESHOLD) -> pd.DataFrame:
    """Apply EXCLU_MAE when stop_prob > threshold; missing data is BLOCK_DATA_MAE."""
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be in (0, 1)")
    out = frame.copy()
    probabilities: list[float] = []
    statuses: list[str] = []
    for _, row in out.iterrows():
        try:
            prob = predict_stop_prob(row)
        except MAEDataUnavailable:
            probabilities.append(np.nan)
            statuses.append("BLOCK_DATA_MAE")
            continue
        probabilities.append(prob)
        statuses.append("EXCLU_MAE" if prob > threshold else "OK")
    out["stop_prob"] = probabilities
    out["mae_status"] = statuses
    out["EXCLU_MAE"] = out["mae_status"].eq("EXCLU_MAE")
    return out


class MAEPredictor:
    """Compatibility wrapper for the supplied V22.1 interface."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        if not 0.0 < threshold < 1.0:
            raise ValueError("threshold must be in (0, 1)")
        self.threshold = threshold

    def predict_stop_prob(self, row: Mapping[str, object] | pd.Series) -> float:
        return predict_stop_prob(row)

    def predict_batch(self, frame: pd.DataFrame) -> pd.DataFrame:
        return apply_mae_filter(frame, self.threshold)

    def audit_stop_reduction(self, backtest: pd.DataFrame) -> dict[str, float | int | None]:
        """Measure observed stop reduction; never manufacture the 33%->18% target."""
        required = {"hit_stop", "EXCLU_MAE"}
        missing = required.difference(backtest.columns)
        if missing:
            raise MAEDataUnavailable(f"BLOCK_DATA_MAE_AUDIT: missing {sorted(missing)}")
        valid = backtest.dropna(subset=["hit_stop", "EXCLU_MAE"]).copy()
        total = len(valid)
        if total == 0:
            return {
                "total_trades": 0,
                "stops_before": 0,
                "stops_rate_before": None,
                "trades_after_filter": 0,
                "stops_after": 0,
                "stops_rate_after": None,
                "stops_avoided": 0,
                "pct_stops_avoided": None,
            }
        hit = valid["hit_stop"].astype(bool)
        kept = ~valid["EXCLU_MAE"].astype(bool)
        stops_before = int(hit.sum())
        after = valid.loc[kept]
        stops_after = int(after["hit_stop"].astype(bool).sum()) if not after.empty else 0
        return {
            "total_trades": total,
            "stops_before": stops_before,
            "stops_rate_before": float(stops_before / total),
            "trades_after_filter": int(len(after)),
            "stops_after": stops_after,
            "stops_rate_after": float(stops_after / len(after)) if len(after) else None,
            "stops_avoided": int(stops_before - stops_after),
            "pct_stops_avoided": float((stops_before - stops_after) / stops_before) if stops_before else None,
        }
