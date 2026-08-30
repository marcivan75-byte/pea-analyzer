from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from v182.audit.pit_loader import PITDataUnavailable, PITLoader
from v182.hebdo.mae_predictor import apply_mae_filter


@dataclass(frozen=True)
class MarketRegime:
    name: str
    tct_multiplier: float
    two_week_return: float


def load_pit_universe(as_of_date: str | pd.Timestamp, root: Path | str = ".") -> pd.DataFrame:
    """Load the ACTION universe strictly from timestamped PIT audit observations."""
    loader = PITLoader(root)
    frame = loader.load_as_of(pd.Timestamp(as_of_date), "ACTION")
    ticker_col = "ticker" if "ticker" in frame.columns else "yahoo_ticker" if "yahoo_ticker" in frame.columns else None
    if ticker_col is None:
        raise PITDataUnavailable("BLOCK_DATA: no validated ticker column in PIT universe")
    out = frame.copy()
    out[ticker_col] = out[ticker_col].astype(str).str.strip().str.upper()
    out = out[out[ticker_col].ne("") & out[ticker_col].ne("NAN")].copy()
    if "scorable" in out.columns:
        out = out[out["scorable"].astype(bool)].copy()
    if out.empty:
        raise PITDataUnavailable("BLOCK_DATA: no scorable ACTION observation in PIT universe")
    return out


def detect_market_regime(cac40_daily: pd.DataFrame) -> MarketRegime:
    """CRASH when CAC40 two-week close return is below -3%."""
    close_col = "close" if "close" in cac40_daily.columns else "Close" if "Close" in cac40_daily.columns else None
    if close_col is None or len(cac40_daily) < 11:
        raise ValueError("CAC40 daily history must provide at least 11 closes")
    close = pd.to_numeric(cac40_daily[close_col], errors="coerce").dropna()
    if len(close) < 11:
        raise ValueError("CAC40 close history incomplete")
    ret_2w = float(close.iloc[-1] / close.iloc[-11] - 1.0)
    if ret_2w < -0.03:
        return MarketRegime("CRASH", 0.5, ret_2w)
    return MarketRegime("NORMAL", 1.0, ret_2w)


def _rsi(series: pd.Series, periods: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(periods, min_periods=periods).mean()
    avg_loss = loss.rolling(periods, min_periods=periods).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _atr_pct(group: pd.DataFrame, periods: int = 14) -> pd.Series:
    high = pd.to_numeric(group["high"], errors="coerce")
    low = pd.to_numeric(group["low"], errors="coerce")
    close = pd.to_numeric(group["close"], errors="coerce")
    prev = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(periods, min_periods=periods).mean()
    return atr / close.replace(0.0, np.nan)


def compute_features_v22(df_daily: pd.DataFrame, sector_map: dict[str, str] | pd.Series | None = None) -> pd.DataFrame:
    """Compute PIT-safe V22 technical features from a long daily panel.

    Required columns: ticker,date,open,high,low,close,volume. The output contains the
    latest available row for each ticker. Weekly RSI is computed on W-FRI closes.
    """
    required = {"ticker", "date", "high", "low", "close", "volume"}
    missing = required.difference(df_daily.columns)
    if missing:
        raise ValueError(f"Missing V22 columns: {sorted(missing)}")
    panel = df_daily.copy()
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce")
    panel = panel.dropna(subset=["date"]).sort_values(["ticker", "date"])
    rows: list[pd.Series] = []
    for ticker, group in panel.groupby("ticker", sort=False):
        group = group.copy().set_index("date")
        close = pd.to_numeric(group["close"], errors="coerce")
        volume = pd.to_numeric(group["volume"], errors="coerce")
        prior_vol = volume.shift(1)
        avg20 = prior_vol.rolling(20, min_periods=20).mean()
        std20 = prior_vol.rolling(20, min_periods=20).std(ddof=0)
        group["vol_z"] = (volume - avg20) / std20.replace(0.0, np.nan)
        group["sma20"] = close.rolling(20, min_periods=20).mean()
        group["sma200"] = close.rolling(200, min_periods=200).mean()
        group["drawdown_4w"] = close / close.rolling(20, min_periods=20).max() - 1.0
        group["mom_26w"] = close / close.shift(126) - 1.0
        group["atr_14_pct"] = _atr_pct(group.reset_index(), 14).to_numpy()
        group["pct_close"] = close.pct_change()
        group["B1_vol_v2"] = ((group["vol_z"] > 3.0) & (group["pct_close"] < -0.015) & (close < group["sma20"])).fillna(False)
        group["B2_daily"] = group["B1_vol_v2"].shift(1).fillna(False).astype(bool)
        weekly_close = close.resample("W-FRI").last().dropna()
        weekly_rsi = _rsi(weekly_close, 14)
        group["rsi_14_hebdo"] = weekly_rsi.reindex(group.index, method="ffill")
        latest = group.iloc[-1].copy()
        latest["ticker"] = ticker
        latest["date"] = group.index[-1]
        rows.append(latest)
    out = pd.DataFrame(rows).reset_index(drop=True)
    if sector_map is not None:
        mapping = sector_map.to_dict() if isinstance(sector_map, pd.Series) else dict(sector_map)
        out["sector"] = out["ticker"].map(mapping)
    elif "sector" not in out.columns:
        out["sector"] = "UNKNOWN"
    return out


def _sector_zscore(frame: pd.DataFrame, column: str) -> pd.Series:
    grouped = frame.groupby("sector")[column]
    mean = grouped.transform("mean")
    std = grouped.transform("std").replace(0.0, np.nan)
    return (frame[column] - mean) / std


def score_universe_v22(df_universe: pd.DataFrame, lasso_weights: dict[str, dict[str, object]] | None = None) -> pd.DataFrame:
    """Sector-neutral ranking with noise, crash-pattern and MAE exclusions."""
    required = {"ticker", "sector", "mom_26w", "vol_z", "drawdown_4w", "close", "sma200", "atr_14_pct"}
    missing = required.difference(df_universe.columns)
    if missing:
        raise ValueError(f"Missing scoring columns: {sorted(missing)}")
    out = df_universe.copy()
    out["mom_26w_sector"] = _sector_zscore(out, "mom_26w")
    out["selection_status"] = "OK"
    if "market_cap_eur_m" in out.columns:
        noisy = (pd.to_numeric(out["market_cap_eur_m"], errors="coerce") < 300.0) & (out["vol_z"] > 2.5)
        out.loc[noisy, "selection_status"] = "BLOCK_NOISE"
    crash_b = (out["vol_z"] > 3.0) & (out["mom_26w_sector"] < -2.0)
    out.loc[crash_b, "selection_status"] = "EXCLU_B_CRASH"
    out = apply_mae_filter(out, 0.45)
    out.loc[out["mae_status"].eq("EXCLU_MAE"), "selection_status"] = "EXCLU_MAE"

    weights = lasso_weights or {}
    score = pd.Series(0.0, index=out.index)
    used = 0
    for feature, spec in weights.items():
        if feature not in out.columns:
            continue
        weight = float(spec.get("weight", 0.0))
        direction = -1.0 if str(spec.get("direction", "LONG")).upper() == "SHORT" else 1.0
        values = pd.to_numeric(out[feature], errors="coerce")
        centered = (values - values.mean()) / values.std(ddof=0) if values.std(ddof=0) not in (0, np.nan) else values * 0.0
        score = score + direction * weight * centered.fillna(0.0)
        used += 1
    out["governed_score"] = score if used else out["mom_26w_sector"].fillna(-np.inf)

    eligible = out[out["selection_status"].eq("OK")].copy()
    eligible = eligible.sort_values(["sector", "governed_score"], ascending=[True, False])
    eligible["sector_rank"] = eligible.groupby("sector").cumcount() + 1
    eligible = eligible[eligible["sector_rank"] <= 2].sort_values("governed_score", ascending=False)
    blocked = out[~out["selection_status"].eq("OK")].copy()
    return pd.concat([eligible, blocked], ignore_index=True, sort=False)
