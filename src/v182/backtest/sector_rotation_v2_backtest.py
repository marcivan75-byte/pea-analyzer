from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd


DEFAULT_HORIZONS = (5, 20, 60, 120, 250)


@dataclass(frozen=True)
class BacktestResult:
    observations: pd.DataFrame
    summary: dict[str, Any]


def _prepare_prices(prices: pd.DataFrame) -> pd.DataFrame:
    required = {"sector", "date", "price"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"MISSING_PRICE_COLUMNS:{sorted(missing)}")
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"], errors="coerce", utc=True)
    p["price"] = pd.to_numeric(p["price"], errors="coerce")
    p = p.dropna(subset=["sector", "date", "price"]).sort_values(["sector", "date"])
    if (p["price"] <= 0).any():
        raise ValueError("NON_POSITIVE_SECTOR_PRICE")
    if p.duplicated(["sector", "date"]).any():
        raise ValueError("DUPLICATE_SECTOR_PRICE_DATE")
    return p


def _prepare_signals(signals: pd.DataFrame) -> pd.DataFrame:
    required = {"sector", "as_of", "model_version"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"MISSING_SIGNAL_COLUMNS:{sorted(missing)}")
    s = signals.copy()
    s["as_of"] = pd.to_datetime(s["as_of"], errors="coerce", utc=True)
    s = s.dropna(subset=["sector", "as_of", "model_version"]).sort_values(["sector", "as_of"])
    if s.duplicated(["sector", "as_of", "model_version"]).any():
        raise ValueError("DUPLICATE_SIGNAL_SNAPSHOT")
    return s


def _aligned_index(prices: pd.DataFrame, sector: str, as_of: pd.Timestamp) -> int | None:
    g = prices.loc[prices["sector"].astype(str) == str(sector)]
    if g.empty:
        return None
    dates = g["date"].to_numpy()
    idx = int(np.searchsorted(dates, np.datetime64(as_of.to_datetime64()), side="left"))
    return idx if idx < len(g) else None


def _forward_path_metrics(group: pd.DataFrame, start_idx: int, horizon: int) -> dict[str, float | None]:
    if start_idx >= len(group):
        return {"forward_return_pct": None, "mae_pct": None, "mfe_pct": None}
    end_idx = start_idx + int(horizon)
    if end_idx >= len(group):
        return {"forward_return_pct": None, "mae_pct": None, "mfe_pct": None}
    start_price = float(group.iloc[start_idx]["price"])
    future = group.iloc[start_idx + 1 : end_idx + 1]["price"].astype(float)
    if future.empty:
        return {"forward_return_pct": None, "mae_pct": None, "mfe_pct": None}
    end_price = float(group.iloc[end_idx]["price"])
    path_returns = (future / start_price - 1.0) * 100.0
    return {
        "forward_return_pct": (end_price / start_price - 1.0) * 100.0,
        "mae_pct": float(path_returns.min()),
        "mfe_pct": float(path_returns.max()),
    }


def _warning_flag(value: Any, name: str) -> bool:
    if isinstance(value, (list, tuple, set)):
        return name in value
    return name in str(value or "")


def evaluate_signals(
    signals: pd.DataFrame,
    sector_prices: pd.DataFrame,
    *,
    benchmark_prices: pd.DataFrame | None = None,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> BacktestResult:
    """Evaluate archived PIT snapshots against future sector prices.

    Signals must already be frozen point-in-time snapshots. This evaluator never
    reconstructs missing historical values and therefore cannot create look-ahead
    on its own. Price alignment uses the first tradable observation on/after as_of.
    """
    s = _prepare_signals(signals)
    p = _prepare_prices(sector_prices)
    benchmark = _prepare_prices(benchmark_prices) if benchmark_prices is not None and not benchmark_prices.empty else None
    horizons = tuple(sorted({int(h) for h in horizons if int(h) > 0}))
    if not horizons:
        raise ValueError("NO_VALID_HORIZONS")

    sector_groups = {str(k): g.reset_index(drop=True) for k, g in p.groupby("sector", sort=False)}
    benchmark_groups = {str(k): g.reset_index(drop=True) for k, g in benchmark.groupby("sector", sort=False)} if benchmark is not None else {}
    rows: list[dict[str, Any]] = []

    for _, signal in s.iterrows():
        sector = str(signal["sector"])
        group = sector_groups.get(sector)
        if group is None or group.empty:
            continue
        dates = group["date"].to_numpy()
        start_idx = int(np.searchsorted(dates, np.datetime64(signal["as_of"].to_datetime64()), side="left"))
        if start_idx >= len(group):
            continue
        base = {
            "sector": sector,
            "as_of": signal["as_of"],
            "model_version": signal["model_version"],
            "entry_date": group.iloc[start_idx]["date"],
            "entry_price": float(group.iloc[start_idx]["price"]),
            "RLS": pd.to_numeric(pd.Series([signal.get("RLS")]), errors="coerce").iloc[0],
            "RARS": pd.to_numeric(pd.Series([signal.get("RARS")]), errors="coerce").iloc[0],
            "AVCR": pd.to_numeric(pd.Series([signal.get("AVCR")]), errors="coerce").iloc[0],
            "DQS": pd.to_numeric(pd.Series([signal.get("DQS")]), errors="coerce").iloc[0],
            "state": signal.get("state"),
            "new_position_action": signal.get("new_position_action"),
            "promising_but_overvalued": _warning_flag(signal.get("warnings"), "PROMISING_BUT_OVERVALUED"),
            "correction_alert": bool(signal.get("correction_alert", False)) or _warning_flag(signal.get("warnings"), "CORRECTION_ALERT"),
        }
        for h in horizons:
            metrics = _forward_path_metrics(group, start_idx, h)
            for key, value in metrics.items():
                base[f"{key}_{h}d"] = value
            if benchmark_groups:
                # Prefer a benchmark row named MARKET; otherwise a same-sector benchmark may be supplied.
                bg = benchmark_groups.get("MARKET") or benchmark_groups.get(sector)
                if bg is not None and not bg.empty:
                    bdates = bg["date"].to_numpy()
                    bstart = int(np.searchsorted(bdates, np.datetime64(signal["as_of"].to_datetime64()), side="left"))
                    bm = _forward_path_metrics(bg, bstart, h)
                    sr = metrics["forward_return_pct"]
                    br = bm["forward_return_pct"]
                    base[f"benchmark_return_pct_{h}d"] = br
                    base[f"excess_return_pct_{h}d"] = (float(sr) - float(br)) if sr is not None and br is not None else None
        rows.append(base)

    observations = pd.DataFrame(rows)
    summary: dict[str, Any] = {
        "status": "OK" if not observations.empty else "NO_MATCHED_OBSERVATIONS",
        "signal_rows": int(len(s)),
        "matched_rows": int(len(observations)),
        "horizons": list(horizons),
    }
    if observations.empty:
        return BacktestResult(observations, summary)

    metrics: dict[str, Any] = {}
    for h in horizons:
        ret_col = f"forward_return_pct_{h}d"
        mae_col = f"mae_pct_{h}d"
        mfe_col = f"mfe_pct_{h}d"
        ex_col = f"excess_return_pct_{h}d"
        valid = pd.to_numeric(observations[ret_col], errors="coerce")
        metrics[str(h)] = {
            "n": int(valid.notna().sum()),
            "mean_return_pct": None if not valid.notna().any() else round(float(valid.mean()), 6),
            "median_return_pct": None if not valid.notna().any() else round(float(valid.median()), 6),
            "hit_rate_pct": None if not valid.notna().any() else round(float((valid > 0).mean() * 100.0), 6),
            "mean_mae_pct": None if mae_col not in observations or not pd.to_numeric(observations[mae_col], errors="coerce").notna().any() else round(float(pd.to_numeric(observations[mae_col], errors="coerce").mean()), 6),
            "mean_mfe_pct": None if mfe_col not in observations or not pd.to_numeric(observations[mfe_col], errors="coerce").notna().any() else round(float(pd.to_numeric(observations[mfe_col], errors="coerce").mean()), 6),
            "mean_excess_return_pct": None if ex_col not in observations or not pd.to_numeric(observations[ex_col], errors="coerce").notna().any() else round(float(pd.to_numeric(observations[ex_col], errors="coerce").mean()), 6),
        }

    warning_study: dict[str, Any] = {}
    for warning in ("promising_but_overvalued", "correction_alert"):
        flagged = observations[warning].astype(bool)
        warning_study[warning] = {"flagged_n": int(flagged.sum()), "unflagged_n": int((~flagged).sum())}
        for h in horizons:
            ret_col = f"forward_return_pct_{h}d"
            mae_col = f"mae_pct_{h}d"
            vals = pd.to_numeric(observations[ret_col], errors="coerce")
            maes = pd.to_numeric(observations[mae_col], errors="coerce")
            warning_study[warning][str(h)] = {
                "flagged_mean_return_pct": None if not vals[flagged].notna().any() else round(float(vals[flagged].mean()), 6),
                "unflagged_mean_return_pct": None if not vals[~flagged].notna().any() else round(float(vals[~flagged].mean()), 6),
                "flagged_mean_mae_pct": None if not maes[flagged].notna().any() else round(float(maes[flagged].mean()), 6),
                "unflagged_mean_mae_pct": None if not maes[~flagged].notna().any() else round(float(maes[~flagged].mean()), 6),
            }

    summary["metrics"] = metrics
    summary["warning_study"] = warning_study
    return BacktestResult(observations, summary)


def threshold_study(observations: pd.DataFrame, *, avcr_thresholds: Iterable[float] = (60, 65, 70, 75, 80), horizon: int = 60) -> pd.DataFrame:
    """Measure warning trade-off without selecting a threshold on a final holdout."""
    ret_col = f"forward_return_pct_{int(horizon)}d"
    mae_col = f"mae_pct_{int(horizon)}d"
    if ret_col not in observations or mae_col not in observations or "AVCR" not in observations:
        return pd.DataFrame()
    avcr = pd.to_numeric(observations["AVCR"], errors="coerce")
    rets = pd.to_numeric(observations[ret_col], errors="coerce")
    maes = pd.to_numeric(observations[mae_col], errors="coerce")
    rows = []
    for threshold in avcr_thresholds:
        flagged = avcr >= float(threshold)
        valid_flag = flagged & rets.notna()
        valid_keep = (~flagged) & rets.notna()
        rows.append(
            {
                "AVCR_threshold": float(threshold),
                "flagged_n": int(valid_flag.sum()),
                "kept_n": int(valid_keep.sum()),
                "flagged_mean_return_pct": None if not valid_flag.any() else float(rets[valid_flag].mean()),
                "kept_mean_return_pct": None if not valid_keep.any() else float(rets[valid_keep].mean()),
                "flagged_mean_mae_pct": None if not (flagged & maes.notna()).any() else float(maes[flagged].mean()),
                "kept_mean_mae_pct": None if not ((~flagged) & maes.notna()).any() else float(maes[~flagged].mean()),
                "missed_upside_pct": None if not valid_flag.any() else float(rets[valid_flag].clip(lower=0).mean()),
            }
        )
    return pd.DataFrame(rows)
