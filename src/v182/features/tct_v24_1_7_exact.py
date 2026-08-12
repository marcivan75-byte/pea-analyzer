from __future__ import annotations

import numpy as np
import pandas as pd


def compute_technical_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Exact technical feature set used by the V24.1.7 T1/T2 source kit.

    This intentionally uses the same `ta` implementations and parameters as the
    audited package: Bollinger(20,2), Stoch(14,3), MACD defaults, RSI14, PSAR,
    SMA20/50/200 and ATR14. Column names are normalised to lower-case.
    """
    if frame is None or len(frame) < 60:
        return frame.copy() if frame is not None else pd.DataFrame()

    try:
        from ta.trend import MACD, PSARIndicator, SMAIndicator
        from ta.momentum import StochasticOscillator, RSIIndicator
        from ta.volatility import BollingerBands, AverageTrueRange
    except ImportError as exc:  # fail closed: do not approximate the normative kit
        raise ImportError("TCT V24.1.7 exact timing requires package 'ta>=0.11.0'") from exc

    df=frame.copy()
    df=df.rename(columns={c:str(c).lower() for c in df.columns})
    required={"open","high","low","close","volume"}
    missing=required-set(df.columns)
    if missing:
        raise ValueError(f"TCT exact OHLCV columns missing: {sorted(missing)}")

    for col in required:
        df[col]=pd.to_numeric(df[col],errors="coerce")
    df=df.sort_index().dropna(subset=["close"])
    if len(df)<60:
        return df

    close,high,low,volume=df["close"],df["high"],df["low"],df["volume"]
    bb=BollingerBands(close=close,window=20,window_dev=2)
    df["bb_high"]=bb.bollinger_hband(); df["bb_low"]=bb.bollinger_lband(); df["bb_mid"]=bb.bollinger_mavg()
    df["bandwidth"]=(df["bb_high"]-df["bb_low"])/df["bb_mid"].replace(0,np.nan)
    df["percent_b"]=bb.bollinger_pband()

    stoch=StochasticOscillator(high=high,low=low,close=close,window=14,smooth_window=3)
    df["stoch_k"]=stoch.stoch(); df["stoch_d"]=stoch.stoch_signal()

    macd=MACD(close=close)
    df["macd"]=macd.macd(); df["macd_signal"]=macd.macd_signal(); df["macd_hist"]=macd.macd_diff()
    df["rsi"]=RSIIndicator(close=close,window=14).rsi()
    df["sar"]=PSARIndicator(high=high,low=low,close=close).psar()
    df["mm20"]=SMAIndicator(close=close,window=20).sma_indicator()
    df["mm50"]=SMAIndicator(close=close,window=50).sma_indicator()
    df["mm200"]=SMAIndicator(close=close,window=200).sma_indicator()
    df["atr_14"]=AverageTrueRange(high=high,low=low,close=close,window=14).average_true_range()
    df["atr_pct"]=df["atr_14"]/close.replace(0,np.nan)
    df["realized_vol_10d"]=close.pct_change().rolling(10).std()*np.sqrt(252)
    df["realized_vol_20d"]=close.pct_change().rolling(20).std()*np.sqrt(252)
    df["vol_ma20"]=volume.rolling(20).mean(); df["vol_ratio"]=volume/df["vol_ma20"].replace(0,np.nan)
    df["rs_5d"]=close/close.shift(5)-1.0; df["rs_10d"]=close/close.shift(10)-1.0
    return df
