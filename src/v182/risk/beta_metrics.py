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
        series = pd.concat(parts).sort_index()
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
) -> tuple[pd.Series | None, dict]:
    eligible = {ticker: to_returns(prices) for ticker, prices in action_prices.items()}
    eligible = {ticker: returns for ticker, returns in eligible.items() if returns.notna().sum() >= min_sessions}
    if len(eligible) < min_constituents:
        return None, {
            "status": "INSUFFICIENT_BENCHMARK_CONSTITUENTS",
            "eligible_constituents": len(eligible),
            "required_constituents": min_constituents,
        }
    matrix = pd.concat(eligible, axis=1).sort_index()
    min_daily = max(3, int(math.ceil(min_constituents * 0.5)))
    benchmark = matrix.mean(axis=1, skipna=True).where(matrix.notna().sum(axis=1) >= min_daily).dropna()
    if benchmark.notna().sum() < min_sessions:
        return None, {
            "status": "INSUFFICIENT_BENCHMARK_SESSIONS",
            "sessions": int(benchmark.notna().sum()),
            "required_sessions": min_sessions,
            "eligible_constituents": len(eligible),
        }
    return benchmark, {
        "status": "OK",
        "label": "PEA_ACTION_EQUAL_WEIGHT_PROXY",
        "eligible_constituents": len(eligible),
        "sessions": int(benchmark.notna().sum()),
        "method": "DAILY_EQUAL_WEIGHT_MEAN_OF_AVAILABLE_ACTION_RETURNS",
    }


def _pair(asset: pd.Series, benchmark: pd.Series, window: int | None = None) -> pd.DataFrame:
    pair = pd.concat([asset, benchmark], axis=1, keys=["asset", "benchmark"]).dropna()
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
    pairs = {window: _pair(asset_returns, benchmark_returns, window) for window in windows}
    result: dict[str, object] = {}
    for window, pair in pairs.items():
        result[f"beta_{window}d"] = _beta(pair, min(min_obs, max(12, window // 2)))
    long_window = windows[-1]
    long_pair = pairs[long_window]
    correlation = _corr(long_pair, min(min_obs, 40))
    result["correlation_252d"] = correlation
    result["r2_252d"] = correlation**2 if correlation is not None else None
    result["upside_beta_252d"] = _beta(long_pair[long_pair["benchmark"] > 0], min_directional_obs)
    result["downside_beta_252d"] = _beta(long_pair[long_pair["benchmark"] < 0], min_directional_obs)
    upside = num(result["upside_beta_252d"])
    downside = num(result["downside_beta_252d"])
    result["downside_upside_beta_ratio"] = (
        downside / upside if upside is not None and downside is not None and upside > 0.05 else None
    )
    stress_corr = None
    if len(long_pair) >= max(30, min_stress_obs):
        threshold = float(long_pair["benchmark"].quantile(stress_quantile))
        stress_corr = _corr(long_pair[long_pair["benchmark"] <= threshold], min_stress_obs)
    result["stress_correlation_252d"] = stress_corr
    betas = [num(result.get(f"beta_{window}d")) for window in windows]
    valid = [value for value in betas if value is not None]
    result["beta_stability_span"] = max(valid) - min(valid) if len(valid) >= 2 else None
    beta_long = num(result.get(f"beta_{long_window}d"))
    result["beta_class"] = _beta_class(beta_long)
    result["beta_reliability"] = _reliability(num(result["r2_252d"]))
    result["sessions_252d"] = int(len(long_pair))
    result["status"] = "OK" if beta_long is not None else "INSUFFICIENT_HISTORY"
    return result


def economic_engine_tags(*values: object) -> list[str]:
    text = " " + " ".join(clean_text(value).lower() for value in values if clean_text(value)) + " "
    tags = [tag for tag, patterns in ENGINE_PATTERNS.items() if any(pattern in text for pattern in patterns)] or ["OTHER"]
    macro: list[str] = []
    for tag in tags:
        macro.extend(MACRO_LINKS.get(tag, ()))
    return sorted(set(tags + macro))


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    union = a | b
    return len(a & b) / len(union) if union else 0.0
