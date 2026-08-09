from __future__ import annotations

import math
import numpy as np
import pandas as pd

from .config import OptimizerConfig


def portfolio_metrics(periods: pd.DataFrame) -> dict[str, float]:
    if periods.empty:
        return {
            "mean_return": math.nan, "annualized_return": math.nan, "annualized_vol": math.nan,
            "max_drawdown": math.nan, "hit_rate": math.nan, "turnover": math.nan, "periods": 0.0,
        }
    r = periods["net_return"].astype(float)
    dates = pd.to_datetime(periods["snapshot_date"])
    med = dates.sort_values().diff().dt.days.dropna().median()
    median_days = max(1.0, float(med if pd.notna(med) else 7.0))
    periods_per_year = 365.25 / median_days
    growth = float(np.prod(1.0 + r.clip(lower=-0.999)))
    ann = growth ** (periods_per_year / max(len(r), 1)) - 1.0 if growth > 0 else -1.0
    vol = float(r.std(ddof=1) * math.sqrt(periods_per_year)) if len(r) > 1 else 0.0
    curve = (1.0 + r).cumprod()
    dd = curve / curve.cummax() - 1.0
    return {
        "mean_return": float(r.mean()), "annualized_return": float(ann), "annualized_vol": float(vol),
        "max_drawdown": float(dd.min()) if len(dd) else 0.0, "hit_rate": float((r > 0).mean()),
        "turnover": float(periods["turnover"].mean()), "periods": float(len(periods)),
    }


def objective(metrics: dict[str, float], cfg: OptimizerConfig) -> float:
    if any(pd.isna(v) for v in metrics.values()):
        return -1e9
    return (
        cfg.objective_return_weight * metrics["annualized_return"]
        + cfg.objective_hit_rate_weight * (metrics["hit_rate"] - 0.5)
        - cfg.objective_drawdown_weight * abs(metrics["max_drawdown"])
        - cfg.objective_vol_weight * metrics["annualized_vol"]
        - cfg.objective_turnover_weight * metrics["turnover"]
    )
