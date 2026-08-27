from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

MISSING_TOKENS = {"", "nan", "none", "n/a", "na", "null", "<na>"}

ENGINE_PATTERNS: dict[str, tuple[str, ...]] = {
    "AI_TECH": ("technology", "tech", "artificial intelligence", " ai ", "cloud", "digital"),
    "SEMICONDUCTORS": ("semiconductor", "semi-conductor", "chip", "microelectron", "processor"),
    "SOFTWARE_CLOUD": ("software", "cloud", "saas"),
    "ENERGY_OIL": ("energy", "oil", "petroleum", "brent", "gas", "utilities"),
    "GOLD_PRECIOUS": ("gold", "precious", "silver", "bullion"),
    "DEFENSE": ("defense", "defence", "aerospace", "military"),
    "BANKS_RATES": ("bank", "financial", "insurance", "financ"),
    "HEALTH": ("health", "pharma", "biotech", "medical"),
    "REAL_ESTATE_RATES": ("real estate", "reit", "property", "immobil"),
    "CONSUMER": ("consumer", "retail", "luxury", "staples", "discretionary"),
    "INDUSTRIAL_CAPEX": ("industrial", "machinery", "capital goods", "construction"),
    "CHINA_EM": ("china", "chinese", "emerging", "emerging markets"),
    "BROAD_EQUITY": ("world", "msci", "s&p", "stoxx", "eurozone", "europe", "global", "broad market"),
}

MACRO_LINKS: dict[str, tuple[str, ...]] = {
    "AI_TECH": ("GROWTH", "LIQUIDITY", "RATES"),
    "SEMICONDUCTORS": ("GROWTH", "LIQUIDITY", "CAPEX", "RATES"),
    "SOFTWARE_CLOUD": ("GROWTH", "LIQUIDITY", "RATES"),
    "ENERGY_OIL": ("ENERGY", "INFLATION", "GEOPOLITICS"),
    "GOLD_PRECIOUS": ("REAL_RATES", "USD", "GEOPOLITICS", "INFLATION"),
    "DEFENSE": ("GEOPOLITICS", "FISCAL_SPENDING"),
    "BANKS_RATES": ("RATES", "YIELD_CURVE", "CREDIT_CYCLE"),
    "HEALTH": ("DEFENSIVE_GROWTH",),
    "REAL_ESTATE_RATES": ("RATES", "CREDIT", "INFLATION"),
    "CONSUMER": ("GROWTH", "INFLATION"),
    "INDUSTRIAL_CAPEX": ("CAPEX", "GROWTH", "INFLATION"),
    "CHINA_EM": ("CHINA_EM", "USD", "GLOBAL_GROWTH"),
    "BROAD_EQUITY": ("GLOBAL_GROWTH", "LIQUIDITY"),
}


def num(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in MISSING_TOKENS else text


def _price_series_from_frame(frame: pd.DataFrame) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    if frame is None or frame.empty:
        return out
    if isinstance(frame.columns, pd.MultiIndex):
        for ticker in pd.Index(frame.columns.get_level_values(0)).dropna().unique():
            try:
                sub = frame[ticker]
            except (KeyError, TypeError):
                continue
            close = next((c for c in ("Close", "Adj Close", "close", "adj close") if c in sub.columns), None)
            if close is None:
                continue
            series = pd.to_numeric(sub[close], errors="coerce").dropna()
            if not series.empty:
                out[str(ticker)] = series
        return out
    close = next((c for c in ("Close", "Adj Close", "close", "adj close") if c in frame.columns), None)
    ticker = clean_text(frame.attrs.get("ticker"))
    if close and ticker:
        series = pd.to_numeric(frame[close], errors="coerce").dropna()
        if not series.empty:
            out[ticker] = series
    return out


def load_cached_prices(cache_dir: Path) -> dict[str, pd.Series]:
    combined: dict[str, list[pd.Series]] = {}
    for path in sorted(cache_dir.glob("history_*.parquet")):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        for ticker, series in _price_series_from_frame(frame).items():
            combined.setdefault(ticker, []).append(series)
    out: dict[str, pd.Series] = {}
    for ticker, parts in combined.items():
        series = pd.concat(parts, sort=False).sort_index()
        series = series[~series.index.duplicated(keep="last")].dropna()
        if not series.empty:
            out[ticker] = series
    return out


def to_returns(prices: pd.Series) -> pd.Series:
    return (
        pd.to_numeric(prices, errors="coerce")
        .pct_change(fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )


def build_common_benchmark(
    action_prices: dict[str, pd.Series],
    *,
    min_sessions: int = 126,
    min_constituents: int = 20,
    min_daily_fraction: float = 0.20,
    winsor_tail: float = 0.05,
    max_abs_daily_return: float = 0.15,
) -> tuple[pd.Series | None, dict]:
    """Build a robust equal-weight PEA Action market proxy.

    The previous raw cross-sectional mean allowed a small number of bad Yahoo
    observations to contaminate the common beta benchmark. This version uses a
    5/95 cross-sectional winsorized mean, requires meaningful daily breadth,
    and fails closed if the resulting broad-market daily move remains
    implausibly large. These are data-quality controls, not alpha parameters.
    """
    eligible = {ticker: to_returns(prices) for ticker, prices in action_prices.items()}
    eligible = {
        ticker: returns
        for ticker, returns in eligible.items()
        if returns.notna().sum() >= min_sessions
    }
    eligible_count = len(eligible)
    if eligible_count < min_constituents:
        return None, {
            "status": "INSUFFICIENT_BENCHMARK_CONSTITUENTS",
            "eligible_constituents": eligible_count,
            "required_constituents": min_constituents,
        }
    if not 0.0 <= winsor_tail < 0.50:
        raise ValueError("winsor_tail must be in [0, 0.5)")
    if not 0.0 < min_daily_fraction <= 1.0:
        raise ValueError("min_daily_fraction must be in (0, 1]")

    matrix = pd.concat(eligible, axis=1, sort=False).sort_index()
    counts = matrix.notna().sum(axis=1)
    min_daily = max(min_constituents, int(math.ceil(eligible_count * min_daily_fraction)))
    valid_dates = counts >= min_daily
    matrix = matrix.loc[valid_dates]
    counts = counts.loc[valid_dates]
    if matrix.empty:
        return None, {
            "status": "INSUFFICIENT_DAILY_BREADTH",
            "eligible_constituents": eligible_count,
            "required_daily_constituents": min_daily,
        }

    lower = matrix.quantile(winsor_tail, axis=1)
    upper = matrix.quantile(1.0 - winsor_tail, axis=1)
    robust_matrix = matrix.clip(lower=lower, upper=upper, axis=0)
    benchmark = robust_matrix.mean(axis=1, skipna=True).dropna()

    if benchmark.notna().sum() < min_sessions:
        return None, {
            "status": "INSUFFICIENT_BENCHMARK_SESSIONS",
            "sessions": int(benchmark.notna().sum()),
            "required_sessions": min_sessions,
            "eligible_constituents": eligible_count,
            "required_daily_constituents": min_daily,
        }

    max_abs = float(benchmark.abs().max())
    p99_abs = float(benchmark.abs().quantile(0.99))
    if not math.isfinite(max_abs) or max_abs > max_abs_daily_return:
        return None, {
            "status": "BENCHMARK_QC_FAILED_EXTREME_DAILY_RETURN",
            "eligible_constituents": eligible_count,
            "sessions": int(len(benchmark)),
            "max_abs_daily_return": max_abs,
            "allowed_max_abs_daily_return": max_abs_daily_return,
            "p99_abs_daily_return": p99_abs,
            "required_daily_constituents": min_daily,
            "min_observed_daily_constituents": int(counts.min()),
        }

    return benchmark, {
        "status": "OK",
        "label": "PEA_ACTION_ROBUST_EQUAL_WEIGHT_PROXY_V2",
        "eligible_constituents": eligible_count,
        "sessions": int(benchmark.notna().sum()),
        "method": "DAILY_5_95_WINSORIZED_EQUAL_WEIGHT_MEAN_OF_AVAILABLE_ACTION_RETURNS",
        "winsor_tail": winsor_tail,
        "required_daily_fraction": min_daily_fraction,
        "required_daily_constituents": min_daily,
        "min_observed_daily_constituents": int(counts.min()),
        "median_observed_daily_constituents": float(counts.median()),
        "max_abs_daily_return": max_abs,
        "p99_abs_daily_return": p99_abs,
        "allowed_max_abs_daily_return": max_abs_daily_return,
    }


def _pair(asset: pd.Series, benchmark: pd.Series, window: int | None = None) -> pd.DataFrame:
    pair = pd.concat([asset, benchmark], axis=1, keys=["asset", "benchmark"], sort=False).dropna()
    return pair.tail(window) if window else pair


def _beta(pair: pd.DataFrame, min_obs: int) -> float | None:
    if len(pair) < min_obs:
        return None
    variance = float(pair["benchmark"].var())
    if not math.isfinite(variance) or variance <= 0:
        return None
    value = float(pair["asset"].cov(pair["benchmark"]) / variance)
    return value if math.isfinite(value) else None


def _corr(pair: pd.DataFrame, min_obs: int) -> float | None:
    if len(pair) < min_obs:
        return None
    asset_std = float(pair["asset"].std())
    benchmark_std = float(pair["benchmark"].std())
    if (
        not math.isfinite(asset_std)
        or not math.isfinite(benchmark_std)
        or asset_std <= 0
        or benchmark_std <= 0
    ):
        return None
    value = float(pair["asset"].corr(pair["benchmark"]))
    return value if math.isfinite(value) else None


def _beta_class(beta: float | None) -> str:
    if beta is None:
        return "MISSING"
    if beta < 0.60:
        return "DEFENSIVE"
    if beta < 0.85:
        return "MODERATE"
    if beta <= 1.10:
        return "MARKET"
    if beta <= 1.30:
        return "DYNAMIC"
    if beta <= 1.60:
        return "AGGRESSIVE"
    return "VERY_AGGRESSIVE"


def _reliability(r2: float | None) -> str:
    if r2 is None:
        return "MISSING"
    if r2 >= 0.60:
        return "HIGH"
    if r2 >= 0.35:
        return "MEDIUM"
    if r2 >= 0.15:
        return "LOW"
    return "VERY_LOW"


def compute_beta_metrics(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    windows: tuple[int, int, int] = (63, 126, 252),
    min_obs: int = 40,
    min_directional_obs: int = 20,
    stress_quantile: float = 0.10,
    min_stress_obs: int = 12,
) -> dict:
    aligned_asset, aligned_benchmark = asset_returns.align(benchmark_returns, join="inner")
    valid_mask = aligned_asset.notna() & aligned_benchmark.notna()
    asset_values = aligned_asset[valid_mask].to_numpy(dtype=float, copy=False)
    benchmark_values = aligned_benchmark[valid_mask].to_numpy(dtype=float, copy=False)

    def array_beta(asset: np.ndarray, benchmark: np.ndarray, required: int) -> float | None:
        if len(asset) < required:
            return None
        benchmark_centered = benchmark - benchmark.mean()
        denominator = float(np.dot(benchmark_centered, benchmark_centered))
        if not math.isfinite(denominator) or denominator <= 0:
            return None
        value = float(np.dot(asset - asset.mean(), benchmark_centered) / denominator)
        return value if math.isfinite(value) else None

    def array_corr(asset: np.ndarray, benchmark: np.ndarray, required: int) -> float | None:
        if len(asset) < required:
            return None
        asset_centered = asset - asset.mean()
        benchmark_centered = benchmark - benchmark.mean()
        denominator = float(
            np.sqrt(
                np.dot(asset_centered, asset_centered)
                * np.dot(benchmark_centered, benchmark_centered)
            )
        )
        if not math.isfinite(denominator) or denominator <= 0:
            return None
        value = float(np.dot(asset_centered, benchmark_centered) / denominator)
        return value if math.isfinite(value) else None

    result: dict[str, object] = {}
    for window in windows:
        result[f"beta_{window}d"] = array_beta(
            asset_values[-window:],
            benchmark_values[-window:],
            min(min_obs, max(12, window // 2)),
        )
    long_window = windows[-1]
    long_asset = asset_values[-long_window:]
    long_benchmark = benchmark_values[-long_window:]
    correlation = array_corr(long_asset, long_benchmark, min(min_obs, 40))
    result["correlation_252d"] = correlation
    result["r2_252d"] = correlation**2 if correlation is not None else None
    result["upside_beta_252d"] = array_beta(
        long_asset[long_benchmark > 0], long_benchmark[long_benchmark > 0], min_directional_obs
    )
    result["downside_beta_252d"] = array_beta(
        long_asset[long_benchmark < 0], long_benchmark[long_benchmark < 0], min_directional_obs
    )
    upside = num(result["upside_beta_252d"])
    downside = num(result["downside_beta_252d"])
    result["downside_upside_beta_ratio"] = (
        downside / upside if upside is not None and downside is not None and upside > 0.05 else None
    )
    stress_corr = None
    if len(long_asset) >= max(30, min_stress_obs):
        threshold = float(np.quantile(long_benchmark, stress_quantile))
        stress_mask = long_benchmark <= threshold
        stress_corr = array_corr(
            long_asset[stress_mask], long_benchmark[stress_mask], min_stress_obs
        )
    result["stress_correlation_252d"] = stress_corr
    betas = [num(result.get(f"beta_{window}d")) for window in windows]
    valid = [value for value in betas if value is not None]
    result["beta_stability_span"] = max(valid) - min(valid) if len(valid) >= 2 else None
    beta_long = num(result.get(f"beta_{long_window}d"))
    result["beta_class"] = _beta_class(beta_long)
    reliability = _reliability(num(result["r2_252d"]))
    result["beta_reliability"] = reliability
    result["sessions_252d"] = int(len(long_asset))

    # A beta with R² < 0.15 is mathematically computable but too weakly linked to
    # the common market factor to support a risk verdict, portfolio beta or stress
    # scenario. Preserve correlation/R² for auditability, but fail closed on all
    # beta-derived fields so extreme low-R² micro-cap estimates cannot pollute the
    # context layer. LOW/MEDIUM/HIGH reliability remains observable and usable.
    if reliability == "VERY_LOW":
        for field in (
            *(f"beta_{window}d" for window in windows),
            "upside_beta_252d",
            "downside_beta_252d",
            "downside_upside_beta_ratio",
            "beta_stability_span",
        ):
            result[field] = None
        result["beta_class"] = "UNRELIABLE"
        result["status"] = "UNRELIABLE_LOW_R2"
    else:
        result["status"] = "OK" if beta_long is not None else "INSUFFICIENT_HISTORY"
    return result


def economic_engine_tags(*values: object) -> list[str]:
    text = " " + " ".join(clean_text(value).lower() for value in values if clean_text(value)) + " "
    tags = [
        tag
        for tag, patterns in ENGINE_PATTERNS.items()
        if any(pattern in text for pattern in patterns)
    ] or ["OTHER"]
    macro: list[str] = []
    for tag in tags:
        macro.extend(MACRO_LINKS.get(tag, ()))
    return sorted(set(tags + macro))


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    union = a | b
    return len(a & b) / len(union) if union else 0.0
