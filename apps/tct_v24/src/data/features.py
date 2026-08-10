import pandas as pd
import numpy as np

try:
    from ta.trend import MACD, PSARIndicator, SMAIndicator
    from ta.momentum import StochasticOscillator, RSIIndicator
    from ta.volatility import BollingerBands, AverageTrueRange
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False

def compute_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule l'ensemble des indicateurs nécessaires au TCT V24.1.4.
    """
    if df is None or len(df) < 60:
        return df

    if not TA_AVAILABLE:
        raise ImportError("La librairie 'ta' est requise : pip install ta")

    df = df.copy()
    
    # Normalisation des colonnes
    col_map = {c: c.lower() for c in df.columns}
    df = df.rename(columns=col_map)
    
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"Colonne manquante : {col}")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # Bollinger
    bb = BollingerBands(close=close, window=20, window_dev=2)
    df["bb_high"] = bb.bollinger_hband()
    df["bb_low"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["bandwidth"] = (df["bb_high"] - df["bb_low"]) / df["bb_mid"]
    df["percent_b"] = bb.bollinger_pband()

    # Stochastic
    stoch = StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
    df["stoch_k"] = stoch.stoch()
    df["stoch_d"] = stoch.stoch_signal()

    # MACD
    macd = MACD(close=close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    # RSI
    df["rsi"] = RSIIndicator(close=close, window=14).rsi()

    # Parabolic SAR
    df["sar"] = PSARIndicator(high=high, low=low, close=close).psar()

    # Moving averages
    df["mm20"] = SMAIndicator(close=close, window=20).sma_indicator()
    df["mm50"] = SMAIndicator(close=close, window=50).sma_indicator()
    df["mm200"] = SMAIndicator(close=close, window=200).sma_indicator()

    # ATR & volatilité
    df["atr_14"] = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
    df["atr_pct"] = df["atr_14"] / close
    df["realized_vol_10d"] = close.pct_change().rolling(10).std() * np.sqrt(252)
    df["realized_vol_20d"] = close.pct_change().rolling(20).std() * np.sqrt(252)

    # Volume
    df["vol_ma20"] = volume.rolling(20).mean()
    df["vol_ratio"] = volume / df["vol_ma20"]

    # Relative strength simple
    df["rs_5d"] = close / close.shift(5) - 1
    df["rs_10d"] = close / close.shift(10) - 1

    return df
