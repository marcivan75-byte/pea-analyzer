"""
Gates / Synergies V21.3 (CDC §6 étape 6)
Vétos, conditions Cœur, squeeze, confirmation événement.
"""

from __future__ import annotations
from typing import Dict, Any
import pandas as pd
import numpy as np

def evaluate_gates(row: pd.Series, score_v21: float) -> Dict[str, Any]:
    """
    Retourne vétos et flags de synergie.
    """
    vetos = []
    flags = []

    # Vétos durs
    if str(row.get("universe_status")) == "REJECT":
        vetos.append("UNIVERSE_REJECT")

    adv = row.get("avg_dollar_volume_20d")
    if pd.notna(adv) and float(adv) < 100_000:
        vetos.append("ILLIQUID")

    # Days = 0/1 avec gap risk extrême
    days = row.get("days_to_earnings")
    p_adv = row.get("p_adverse")
    if pd.notna(days) and float(days) <= 1 and pd.notna(p_adv) and float(p_adv) >= 0.35:
        vetos.append("EARNINGS_GAP_RISK")

    # Synergies positives (bonus flags, pas redistribution de poids)
    setup = str(row.get("setup") or "")
    if setup == "T2_CONFIRMATION":
        flags.append("T2_CONFIRM")
    if setup == "T1":
        flags.append("T1_SETUP")

    se = row.get("score_earnings_proximity")
    if pd.notna(se) and float(se) >= 80:
        flags.append("EARNINGS_STRONG")

    if pd.notna(row.get("bandwidth")) and float(row["bandwidth"]) < 0.05:
        flags.append("SQUEEZE_TIGHT")

    if pd.notna(row.get("vol_ratio")) and float(row["vol_ratio"]) >= 2.0:
        flags.append("VOLUME_CLIMACTIC")

    if row.get("squeeze_candidate") is True or str(row.get("squeeze_candidate")).lower() == "true":
        flags.append("SQUEEZE_CANDIDATE")
    if row.get("squeeze_strong") is True or str(row.get("squeeze_strong")).lower() == "true":
        flags.append("SQUEEZE_STRONG")
    sp = row.get("squeeze_pressure")
    if pd.notna(sp) and float(sp) >= 75:
        flags.append("SQUEEZE_PRESSURE_HIGH")

    # Bonus synergie borné (CDC : bonus dans bornes configurées)
    synergy_bonus = 0.0
    if "T2_CONFIRM" in flags and "EARNINGS_STRONG" in flags:
        synergy_bonus += 5.0
        flags.append("SYNERGY_T2_EARNINGS")
    if "SQUEEZE_TIGHT" in flags and "VOLUME_CLIMACTIC" in flags:
        synergy_bonus += 3.0
        flags.append("SYNERGY_SQUEEZE_VOL")
    if "T1_SETUP" in flags and "SQUEEZE_TIGHT" in flags:
        synergy_bonus += 2.0
    if "SQUEEZE_STRONG" in flags and ("T2_CONFIRM" in flags or "EARNINGS_STRONG" in flags):
        synergy_bonus += 4.0
        flags.append("SYNERGY_SQUEEZE_SETUP")
    if "SQUEEZE_CANDIDATE" in flags and "VOLUME_CLIMACTIC" in flags:
        synergy_bonus += 2.0

    synergy_bonus = float(np.clip(synergy_bonus, 0, 10))

    score_after = float(np.clip(score_v21 + synergy_bonus, 0, 100))

    return {
        "vetos": vetos,
        "flags": flags,
        "synergy_bonus": synergy_bonus,
        "score_after_synergy": round(score_after, 2),
        "has_veto": len(vetos) > 0,
    }
