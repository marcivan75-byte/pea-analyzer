from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GlobalMarketSnapshot:
    risk_on_score: float | None
    shock_magnitude_score: float | None
    completed_returns_pct: dict[str, float | None]
    one_shot_returns_pct: dict[str, float | None]
    source: str
    errors: tuple[str, ...]


def _finite(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clip(value: float) -> float:
    return float(np.clip(value, 0.0, 100.0))


def _extract_close_series(raw: pd.DataFrame, symbol: str) -> pd.Series:
    if raw is None or raw.empty:
        return pd.Series(dtype=float)
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            try:
                candidate = raw["Close"][symbol]
            except (KeyError, TypeError):
                candidate = None
            if candidate is not None:
                return pd.to_numeric(candidate, errors="coerce").dropna()
        if symbol in raw.columns.get_level_values(0):
            try:
                sub = raw[symbol]
            except (KeyError, TypeError):
                sub = None
            if sub is not None and "Close" in sub.columns:
                return pd.to_numeric(sub["Close"], errors="coerce").dropna()
    if "Close" in raw.columns and raw.shape[1] <= 8:
        return pd.to_numeric(raw["Close"], errors="coerce").dropna()
    return pd.Series(dtype=float)


def _latest_return_pct(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    previous = _finite(clean.iloc[-2])
    latest = _finite(clean.iloc[-1])
    if previous is None or latest is None or previous == 0:
        return None
    return float((latest / previous - 1.0) * 100.0)


def _risk_component(return_pct: float | None, *, inverse: bool = False) -> float | None:
    if return_pct is None:
        return None
    value = -return_pct if inverse else return_pct
    return _clip(50.0 + value / 2.0 * 50.0)


def fetch_global_market_snapshot(cfg: dict, *, phase: str | None = None) -> GlobalMarketSnapshot:
    """Fetch one compact 1d snapshot; PREOPEN may include one-shot US futures.

    No 1m/5m bars are requested. Futures/FX/commodities are sampled once and
    explicitly stored as one-shot context rather than completed cash sessions.
    """
    import yfinance as yf

    spec = cfg["global_market"]
    completed = dict(spec.get("completed_session_symbols", {}))
    one_shot = dict(spec.get("one_shot_context_symbols", {}))
    symbols = list(dict.fromkeys([*completed.values(), *one_shot.values()]))
    if not symbols:
        return GlobalMarketSnapshot(None, None, {}, {}, "YFINANCE_DAILY_SNAPSHOT", ("NO_SYMBOLS_CONFIGURED",))

    errors: list[str] = []
    try:
        raw = yf.download(
            tickers=symbols,
            period=str(cfg["data_policy"].get("global_snapshot_period", "5d")),
            interval=str(cfg["data_policy"].get("global_snapshot_interval", "1d")),
            auto_adjust=False,
            actions=False,
            group_by="column",
            threads=True,
            progress=False,
        )
    except Exception as exc:
        return GlobalMarketSnapshot(
            None,
            None,
            {k: None for k in completed},
            {k: None for k in one_shot},
            "YFINANCE_DAILY_SNAPSHOT",
            (f"DOWNLOAD:{type(exc).__name__}:{str(exc)[:160]}",),
        )

    completed_returns: dict[str, float | None] = {}
    one_shot_returns: dict[str, float | None] = {}
    for label, symbol in completed.items():
        value = _latest_return_pct(_extract_close_series(raw, symbol))
        completed_returns[label] = value
        if value is None:
            errors.append(f"MISSING_COMPLETED:{label}:{symbol}")
    for label, symbol in one_shot.items():
        value = _latest_return_pct(_extract_close_series(raw, symbol))
        one_shot_returns[label] = value
        if value is None:
            errors.append(f"MISSING_ONESHOT:{label}:{symbol}")

    risk_weights = spec.get("risk_on_weights", {})
    components: list[tuple[float, float]] = []
    mapping = {
        "SP500": completed_returns.get("SP500"),
        "NASDAQ": completed_returns.get("NASDAQ"),
        "RUSSELL2000": completed_returns.get("RUSSELL2000"),
        "NIKKEI": completed_returns.get("NIKKEI"),
        "VIX_INVERSE": completed_returns.get("VIX"),
    }
    for key, weight in risk_weights.items():
        score = _risk_component(mapping.get(key), inverse=(key == "VIX_INVERSE"))
        if score is not None and float(weight) > 0:
            components.append((score, float(weight)))
    risk_on = None
    if components:
        observed_weight = sum(weight for _, weight in components)
        risk_on = sum(score * weight for score, weight in components) / observed_weight

    if str(phase or "").upper() == "PREOPEN":
        futures_scores = [
            _risk_component(one_shot_returns.get("SP500_FUTURE")),
            _risk_component(one_shot_returns.get("NASDAQ_FUTURE")),
        ]
        futures_scores = [x for x in futures_scores if x is not None]
        if futures_scores:
            future_score = float(np.mean(futures_scores))
            overlay = float(spec.get("preopen_futures_overlay_weight", 0.20))
            overlay = float(np.clip(overlay, 0.0, 0.40))
            risk_on = future_score if risk_on is None else (1.0 - overlay) * risk_on + overlay * future_score

    magnitudes: list[float] = []
    for value in [*completed_returns.values(), *one_shot_returns.values()]:
        if value is not None:
            magnitudes.append(min(abs(float(value)) / 2.5 * 100.0, 100.0))
    shock = float(np.mean(sorted(magnitudes, reverse=True)[:4])) if magnitudes else None

    return GlobalMarketSnapshot(
        None if risk_on is None else round(float(risk_on), 4),
        None if shock is None else round(float(shock), 4),
        completed_returns,
        one_shot_returns,
        "YFINANCE_DAILY_SNAPSHOT_ONCE",
        tuple(errors),
    )
