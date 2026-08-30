from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd


def predict_stop_prob(row: Mapping[str, object] | pd.Series) -> float:
    """Heuristic stop-risk probability from the forensic V22.1 audit.

    This is a governed deterministic filter, not a trained probability model. Missing
    required inputs fail closed instead of being imputed.
    """
    required = ("vol_z", "drawdown_4w", "close", "sma200", "atr_14_pct")
    values: dict[str, float] = {}
    for key in required:
        value = row.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 1.0
        if not np.isfinite(number):
            return 1.0
        values[key] = number

    score = 0.0
    score += 2.0 if values["vol_z"] > 4.0 else 0.0
    score += 1.5 if values["drawdown_4w"] < -0.12 else 0.0
    score += 1.0 if values["close"] < values["sma200"] else 0.0
    score += max(values["atr_14_pct"], 0.0) * 10.0
    return 1.0 / (1.0 + math.exp(-(score - 3.0)))


def apply_mae_filter(frame: pd.DataFrame, threshold: float = 0.45) -> pd.DataFrame:
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be in (0, 1)")
    out = frame.copy()
    out["stop_prob"] = [predict_stop_prob(row) for _, row in out.iterrows()]
    out["mae_status"] = np.where(out["stop_prob"] > threshold, "EXCLU_MAE", "OK")
    return out
