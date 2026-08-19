from __future__ import annotations

import json

import pandas as pd

from v182.reporting import tct_v24_4_pit_validator as base


def _spearman_without_scipy(x: pd.Series, y: pd.Series) -> float | None:
    """Spearman rho = Pearson correlation of average ranks, without SciPy."""
    pair = pd.DataFrame(
        {
            "x": pd.to_numeric(x, errors="coerce"),
            "y": pd.to_numeric(y, errors="coerce"),
        }
    ).dropna()
    if len(pair) < 3 or pair["x"].nunique() < 2 or pair["y"].nunique() < 2:
        return None
    rank_x = pair["x"].rank(method="average")
    rank_y = pair["y"].rank(method="average")
    value = rank_x.corr(rank_y, method="pearson")
    if pd.isna(value):
        return None
    return float(value)


# Keep the full validator implementation in one module while replacing only
# the optional-SciPy correlation primitive at runtime.
base._spearman = _spearman_without_scipy

validate_ledger = base.validate_ledger
run = base.run


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
