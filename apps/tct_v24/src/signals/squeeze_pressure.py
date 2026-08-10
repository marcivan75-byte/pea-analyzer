"""
Squeeze Pressure Score (0-100)
Combine :
- Short interest AMF/Euronext (pression de couverture)
- Compression Bollinger (bandwidth)
- Volume climactique (vol_ratio)
- Optionnel : days_to_cover, n_holders, setup T1/T2

Utilisé par :
- Pilier V21.3 volume_flow_squeeze
- Comité / flags SQUEEZE_CANDIDATE
- Decision Engine (synergies)
"""

from __future__ import annotations

from typing import Dict, Any, Optional
import numpy as np
import pandas as pd


def _f(row: pd.Series, key: str, default: float = np.nan) -> float:
    try:
        v = row.get(key, default)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def compute_days_to_cover(
    short_interest_pct: float,
    avg_dollar_volume: float,
    close: float,
    shares_out: Optional[float] = None,
) -> float:
    """
    Proxy days_to_cover.
    Si shares_out disponible : (SI% / 100 * shares_out) / (ADV_shares)
    Sinon proxy via dollar volume : moins précis, retourne NaN si données insuffisantes.
    """
    if np.isnan(short_interest_pct) or short_interest_pct <= 0:
        return np.nan
    if shares_out is not None and not np.isnan(shares_out) and shares_out > 0:
        if np.isnan(avg_dollar_volume) or np.isnan(close) or close <= 0 or avg_dollar_volume <= 0:
            return np.nan
        adv_shares = avg_dollar_volume / close
        if adv_shares <= 0:
            return np.nan
        short_shares = (short_interest_pct / 100.0) * shares_out
        return float(short_shares / adv_shares)
    return np.nan


def compute_squeeze_pressure(row: pd.Series) -> Dict[str, Any]:
    """
    Score 0-100 + composantes + flag candidat.
    """
    si = _f(row, "short_interest")
    bw = _f(row, "bandwidth")
    vr = _f(row, "vol_ratio", 1.0)
    holders = _f(row, "short_n_holders")
    setup = str(row.get("setup") or "")
    close = _f(row, "close")
    adv = _f(row, "avg_dollar_volume_20d")
    shares = _f(row, "shares_outstanding")
    if np.isnan(shares):
        shares = _f(row, "shares_out")

    # --- Composante Short Interest (0-100) ---
    if np.isnan(si):
        si_score = 30.0  # neutre bas (donnée manquante ≠ zéro pression)
        si_present = False
    else:
        si_present = True
        if si >= 20:
            si_score = 95.0
        elif si >= 15:
            si_score = 85.0
        elif si >= 10:
            si_score = 70.0
        elif si >= 5:
            si_score = 55.0
        elif si >= 2:
            si_score = 40.0
        else:
            si_score = 25.0
        # Bonus multi-holders (pression dispersée)
        if not np.isnan(holders) and holders >= 5 and si >= 8:
            si_score = min(100.0, si_score + 5.0)

    # --- Composante Compression BB (0-100) ---
    if np.isnan(bw):
        bw_score = 30.0
        bw_present = False
    else:
        bw_present = True
        # bandwidth typique 0.02-0.15 ; bas = compression
        bw_score = float(np.clip(100.0 - bw * 700.0, 5.0, 98.0))

    # --- Composante Volume (0-100) ---
    if np.isnan(vr):
        vol_score = 40.0
    else:
        vol_score = float(np.clip(vr / 3.0 * 100.0, 0.0, 100.0))

    # --- Days to cover (bonus) ---
    dtc = compute_days_to_cover(si, adv, close, shares if not np.isnan(shares) else None)
    dtc_bonus = 0.0
    if not np.isnan(dtc):
        if dtc >= 5:
            dtc_bonus = 10.0
        elif dtc >= 3:
            dtc_bonus = 6.0
        elif dtc >= 2:
            dtc_bonus = 3.0

    # --- Setup bonus ---
    setup_bonus = 0.0
    if setup == "T2_CONFIRMATION":
        setup_bonus = 8.0
    elif setup == "T1":
        setup_bonus = 4.0

    # Pondération interne du score Squeeze Pressure
    # SI 40% + Bandwidth 35% + Volume 25%  (+ bonuses bornés)
    base = 0.40 * si_score + 0.35 * bw_score + 0.25 * vol_score
    score = float(np.clip(base + dtc_bonus + setup_bonus, 0.0, 100.0))

    # Flag candidat squeeze
    candidate = bool(
        score >= 65
        and si_present
        and not np.isnan(si)
        and si >= 8
        and bw_present
        and not np.isnan(bw)
        and bw <= 0.08
    )
    strong = bool(
        score >= 78
        and not np.isnan(si)
        and si >= 12
        and not np.isnan(vr)
        and vr >= 1.8
    )

    return {
        "squeeze_pressure": round(score, 2),
        "squeeze_si_score": round(si_score, 2),
        "squeeze_bw_score": round(bw_score, 2),
        "squeeze_vol_score": round(vol_score, 2),
        "days_to_cover": None if np.isnan(dtc) else round(float(dtc), 2),
        "squeeze_candidate": candidate,
        "squeeze_strong": strong,
    }


def apply_squeeze_pressure(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute les colonnes squeeze pressure sur un DataFrame de signaux."""
    if df is None or df.empty:
        return df
    rows = df.apply(compute_squeeze_pressure, axis=1, result_type="expand")
    out = df.copy()
    for c in rows.columns:
        out[c] = rows[c]
    return out
