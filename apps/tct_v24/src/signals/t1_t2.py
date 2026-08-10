import pandas as pd
import numpy as np
from typing import Optional, Dict

def check_tct_with_bonus(
    df: pd.DataFrame,
    last_T1_bandwidth: Optional[float] = None,
    ratio: float = 1.07,
    bonus_t1: int = 15,
    bonus_t2: int = 30
) -> Dict:
    """
    Détection T1 / T2 corrigée et robuste.
    - Séparation claire des conditions
    - Gestion des NaN et données insuffisantes
    """
    if df is None or len(df) < 110:
        return {
            "bonus": 0,
            "setup": None,
            "last_T1_bandwidth": last_T1_bandwidth,
            "current_bandwidth": np.nan,
            "seuil_squeeze": np.nan
        }

    required = ["bandwidth", "close", "volume", "bb_high", "stoch_k", "stoch_d",
                "macd", "macd_signal", "rsi", "sar", "mm50"]
    for col in required:
        if col not in df.columns:
            return {
                "bonus": 0,
                "setup": None,
                "last_T1_bandwidth": last_T1_bandwidth,
                "current_bandwidth": np.nan,
                "seuil_squeeze": np.nan
            }

    bw = df["bandwidth"]
    close = df["close"]
    vol = df["volume"]

    # Squeeze : 10 bougies sous percentile 20 des 100 derniers
    seuil = bw.rolling(100).quantile(0.20).iloc[-1]
    if pd.isna(seuil):
        return {
            "bonus": 0,
            "setup": None,
            "last_T1_bandwidth": last_T1_bandwidth,
            "current_bandwidth": bw.iloc[-1],
            "seuil_squeeze": np.nan
        }

    squeeze_10 = bool((bw.iloc[-10:] < seuil).all())

    vol_ma20 = vol.rolling(20).mean().iloc[-1]
    vol_ok = bool(vol.iloc[-1] > vol_ma20 * 1.10) if not pd.isna(vol_ma20) else False

    sortie_bb = bool(
        close.iloc[-2] <= df["bb_high"].iloc[-2] and
        close.iloc[-1] > df["bb_high"].iloc[-1]
    )
    bb_ecartent = bool(bw.iloc[-1] > bw.iloc[-2])

    # Conditions communes (sans MACD)
    try:
        base_common = all([
            squeeze_10,
            vol_ok,
            sortie_bb,
            bb_ecartent,
            df["stoch_k"].iloc[-1] > df["stoch_d"].iloc[-1],
            df["rsi"].iloc[-1] < 70,
            df["stoch_k"].iloc[-1] < 70,
            close.iloc[-1] > df["sar"].iloc[-1],
            close.iloc[-1] > df["mm50"].iloc[-1]
        ])
    except Exception:
        base_common = False

    current_bw = float(bw.iloc[-1]) if not pd.isna(bw.iloc[-1]) else np.nan
    bonus, setup = 0, None
    new_last = last_T1_bandwidth

    # T1 : MACD encore sous le signal
    if base_common and df["macd"].iloc[-1] < df["macd_signal"].iloc[-1]:
        bonus, setup = bonus_t1, "T1"
        new_last = current_bw

    # T2 : confirmation après un T1 + ratio + MACD croisé
    elif (base_common
          and last_T1_bandwidth is not None
          and not pd.isna(last_T1_bandwidth)
          and df["macd"].iloc[-1] > df["macd_signal"].iloc[-1]
          and close.iloc[-1] > df["bb_high"].iloc[-1]
          and current_bw >= last_T1_bandwidth * ratio):
        bonus, setup = bonus_t2, "T2_CONFIRMATION"

    return {
        "bonus": bonus,
        "setup": setup,
        "last_T1_bandwidth": new_last,
        "current_bandwidth": current_bw,
        "seuil_squeeze": float(seuil) if not pd.isna(seuil) else np.nan
    }
