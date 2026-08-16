from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

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
    prepared = prices.copy()
    prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce", utc=True)
    prepared["price"] = pd.to_numeric(prepared["price"], errors="coerce")
    prepared = prepared.dropna(subset=["sector", "date", "price"]).sort_values(["sector", "date"])
    if (prepared["price"] <= 0).any():
        raise ValueError("NON_POSITIVE_SECTOR_PRICE")
    if prepared.duplicated(["sector", "date"]).any():
        raise ValueError("DUPLICATE_SECTOR_PRICE_DATE")
    return prepared


def _prepare_signals(signals: pd.DataFrame) -> pd.DataFrame:
    required = {"sector", "as_of", "model_version"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"MISSING_SIGNAL_COLUMNS:{sorted(missing)}")
    prepared = signals.copy()
    prepared["as_of"] = pd.to_datetime(prepared["as_of"], errors="coerce", utc=True)
    prepared = prepared.dropna(subset=["sector", "as_of", "model_version"]).sort_values(["sector", "as_of"])
    if prepared.duplicated(["sector", "as_of", "model_version"]).any():
        raise ValueError("DUPLICATE_SIGNAL_SNAPSHOT")
    return prepared


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


def _aligned_start(group: pd.DataFrame, as_of: pd.Timestamp) -> int | None:
    """First tradable row on/after the UTC signal timestamp, preserving timezone semantics."""
    idx = int(group["date"].searchsorted(as_of, side="left"))
    return idx if idx < len(group) else None


def evaluate_signals(
    signals: pd.DataFrame,
    sector_prices: pd.DataFrame,
    *,
    benchmark_prices: pd.DataFrame | None = None,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> BacktestResult:
    """Evaluate frozen PIT snapshots against later prices without reconstructing missing history."""
    prepared_signals = _prepare_signals(signals)
    prepared_prices = _prepare_prices(sector_prices)
    benchmark = _prepare_prices(benchmark_prices) if benchmark_prices is not None and not benchmark_prices.empty else None
    horizons = tuple(sorted({int(horizon) for horizon in horizons if int(horizon) > 0}))
    if not horizons:
        raise ValueError("NO_VALID_HORIZONS")

    sector_groups = {
        str(key): group.reset_index(drop=True)
        for key, group in prepared_prices.groupby("sector", sort=False)
    }
    benchmark_groups = (
        {str(key): group.reset_index(drop=True) for key, group in benchmark.groupby("sector", sort=False)}
        if benchmark is not None
        else {}
    )
    rows: list[dict[str, Any]] = []

    for _, signal in prepared_signals.iterrows():
        sector = str(signal["sector"])
        group = sector_groups.get(sector)
        if group is None or group.empty:
            continue
        start_idx = _aligned_start(group, signal["as_of"])
        if start_idx is None:
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
            "correction_alert": bool(signal.get("correction_alert", False))
            or _warning_flag(signal.get("warnings"), "CORRECTION_ALERT"),
        }
        for horizon in horizons:
            metrics = _forward_path_metrics(group, start_idx, horizon)
            for key, value in metrics.items():
                base[f"{key}_{horizon}d"] = value
            if benchmark_groups:
                benchmark_group = benchmark_groups.get("MARKET")
                if benchmark_group is None:
                    benchmark_group = benchmark_groups.get(sector)
                if benchmark_group is not None and not benchmark_group.empty:
                    benchmark_start = _aligned_start(benchmark_group, signal["as_of"])
                    if benchmark_start is not None:
                        benchmark_metrics = _forward_path_metrics(benchmark_group, benchmark_start, horizon)
                        sector_return = metrics["forward_return_pct"]
                        benchmark_return = benchmark_metrics["forward_return_pct"]
                        base[f"benchmark_return_pct_{horizon}d"] = benchmark_return
                        base[f"excess_return_pct_{horizon}d"] = (
                            float(sector_return) - float(benchmark_return)
                            if sector_return is not None and benchmark_return is not None
                            else None
                        )
        rows.append(base)

    observations = pd.DataFrame(rows)
    summary: dict[str, Any] = {
        "status": "OK" if not observations.empty else "NO_MATCHED_OBSERVATIONS",
        "signal_rows": int(len(prepared_signals)),
        "matched_rows": int(len(observations)),
        "horizons": list(horizons),
    }
    if observations.empty:
        return BacktestResult(observations, summary)

    metrics_summary: dict[str, Any] = {}
    for horizon in horizons:
        return_column = f"forward_return_pct_{horizon}d"
        mae_column = f"mae_pct_{horizon}d"
        mfe_column = f"mfe_pct_{horizon}d"
        excess_column = f"excess_return_pct_{horizon}d"
        returns = pd.to_numeric(observations[return_column], errors="coerce")
        maes = pd.to_numeric(observations[mae_column], errors="coerce")
        mfes = pd.to_numeric(observations[mfe_column], errors="coerce")
        excess = pd.to_numeric(observations[excess_column], errors="coerce") if excess_column in observations else pd.Series(dtype=float)
        metrics_summary[str(horizon)] = {
            "n": int(returns.notna().sum()),
            "mean_return_pct": None if not returns.notna().any() else round(float(returns.mean()), 6),
            "median_return_pct": None if not returns.notna().any() else round(float(returns.median()), 6),
            "hit_rate_pct": None if not returns.notna().any() else round(float((returns > 0).mean() * 100.0), 6),
            "mean_mae_pct": None if not maes.notna().any() else round(float(maes.mean()), 6),
            "mean_mfe_pct": None if not mfes.notna().any() else round(float(mfes.mean()), 6),
            "mean_excess_return_pct": None if not excess.notna().any() else round(float(excess.mean()), 6),
        }

    warning_study: dict[str, Any] = {}
    for warning in ("promising_but_overvalued", "correction_alert"):
        flagged = observations[warning].astype(bool)
        warning_study[warning] = {
            "flagged_n": int(flagged.sum()),
            "unflagged_n": int((~flagged).sum()),
        }
        for horizon in horizons:
            return_column = f"forward_return_pct_{horizon}d"
            mae_column = f"mae_pct_{horizon}d"
            returns = pd.to_numeric(observations[return_column], errors="coerce")
            maes = pd.to_numeric(observations[mae_column], errors="coerce")
            warning_study[warning][str(horizon)] = {
                "flagged_mean_return_pct": None if not returns[flagged].notna().any() else round(float(returns[flagged].mean()), 6),
                "unflagged_mean_return_pct": None if not returns[~flagged].notna().any() else round(float(returns[~flagged].mean()), 6),
                "flagged_mean_mae_pct": None if not maes[flagged].notna().any() else round(float(maes[flagged].mean()), 6),
                "unflagged_mean_mae_pct": None if not maes[~flagged].notna().any() else round(float(maes[~flagged].mean()), 6),
            }

    summary["metrics"] = metrics_summary
    summary["warning_study"] = warning_study
    return BacktestResult(observations, summary)


def threshold_study(
    observations: pd.DataFrame,
    *,
    avcr_thresholds: Iterable[float] = (60, 65, 70, 75, 80),
    horizon: int = 60,
) -> pd.DataFrame:
    """Measure warning trade-off without selecting a threshold on a final holdout."""
    return_column = f"forward_return_pct_{int(horizon)}d"
    mae_column = f"mae_pct_{int(horizon)}d"
    if return_column not in observations or mae_column not in observations or "AVCR" not in observations:
        return pd.DataFrame()
    avcr = pd.to_numeric(observations["AVCR"], errors="coerce")
    returns = pd.to_numeric(observations[return_column], errors="coerce")
    maes = pd.to_numeric(observations[mae_column], errors="coerce")
    rows = []
    for threshold in avcr_thresholds:
        flagged = avcr >= float(threshold)
        valid_flagged = flagged & returns.notna()
        valid_kept = (~flagged) & returns.notna()
        rows.append(
            {
                "AVCR_threshold": float(threshold),
                "flagged_n": int(valid_flagged.sum()),
                "kept_n": int(valid_kept.sum()),
                "flagged_mean_return_pct": None if not valid_flagged.any() else float(returns[valid_flagged].mean()),
                "kept_mean_return_pct": None if not valid_kept.any() else float(returns[valid_kept].mean()),
                "flagged_mean_mae_pct": None if not (flagged & maes.notna()).any() else float(maes[flagged].mean()),
                "kept_mean_mae_pct": None if not ((~flagged) & maes.notna()).any() else float(maes[~flagged].mean()),
                "missed_upside_pct": None if not valid_flagged.any() else float(returns[valid_flagged].clip(lower=0).mean()),
            }
        )
    return pd.DataFrame(rows)
