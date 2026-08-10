"""
Scoring V21.3 – 9 piliers (CDC TCT EXPLOSIF DATA-RICH)
PRINCIPE NON NÉGOCIABLE :
- Poids fixes au niveau supérieur
- Pilier manquant = 0 (poids JAMAIS redistribué)
- Aucun 50 neutre imputé
- Score ≠ probabilité
"""

from __future__ import annotations
from typing import Dict, Optional, Any
import numpy as np
import pandas as pd

# Pondération cible V21.3 (CDC)
WEIGHTS_V21_3 = {
    "technique":          0.30,
    "catalyseurs":        0.24,
    "volume_flow_squeeze":0.19,
    "news_secteur":       0.07,
    "revisions":          0.08,
    "risque_liquidite":   0.06,
    "valorisation":       0.03,
    "corporate_support":  0.02,
    "macro_rotation":     0.01,
}

assert abs(sum(WEIGHTS_V21_3.values()) - 1.0) < 1e-9

# Multiplicateurs de fraîcheur (CDC §6.2)
FRESHNESS_MULT = {
    "FRESH": 1.00,
    "AGING": 0.85,
    "STALE_WARNING": 0.75,
    "EXPIRED": 0.0,
}


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(np.clip(x, lo, hi))


def score_technique(row: pd.Series) -> Optional[float]:
    """Pilier Technique 30 % – indicateurs + setups T1/T2."""
    vals = []
    if pd.notna(row.get("rsi")):
        rsi = float(row["rsi"])
        # Zone favorable breakout: 50-70
        vals.append(100 - abs(rsi - 60) * 2)
    if pd.notna(row.get("vol_ratio")):
        vr = float(row["vol_ratio"])
        vals.append(_clip(vr / 2.5 * 80, 0, 100))
    setup = str(row.get("setup") or "")
    if setup == "T2_CONFIRMATION":
        vals.append(92.0)
    elif setup == "T1":
        vals.append(78.0)
    if pd.notna(row.get("percent_b")):
        pb = float(row["percent_b"])
        vals.append(_clip(50 + pb * 50, 0, 100))
    if not vals:
        return None  # manquant → 0 contribution
    return _clip(float(np.mean(vals)))


def score_catalyseurs(row: pd.Series) -> Optional[float]:
    """Pilier Catalyseurs 24 % – earnings, events, cata score."""
    vals = []
    se = row.get("score_earnings_proximity")
    if pd.notna(se):
        vals.append(float(se))
    sc = row.get("score_cata")
    if pd.notna(sc):
        vals.append(float(sc))
    days = row.get("days_to_earnings")
    if pd.notna(days) and float(days) <= 5:
        vals.append(75.0)
    if not vals:
        return None
    return _clip(float(np.mean(vals)))


def score_volume_flow_squeeze(row: pd.Series) -> Optional[float]:
    """Pilier Volume/Flow/Squeeze 19 % – priorise Squeeze Pressure si dispo."""
    # Score dédié Squeeze Pressure (SI + BB + Volume)
    if pd.notna(row.get("squeeze_pressure")):
        return _clip(float(row["squeeze_pressure"]))
    vals = []
    if pd.notna(row.get("vol_ratio")):
        vals.append(_clip(float(row["vol_ratio"]) / 3.0 * 100, 0, 100))
    if pd.notna(row.get("bandwidth")):
        bw = float(row["bandwidth"])
        vals.append(_clip(100 - bw * 700, 5, 98))
    if pd.notna(row.get("short_interest")):
        si = float(row["short_interest"])
        vals.append(_clip(si / 20.0 * 100, 0, 100))
    if pd.notna(row.get("score_squeeze")):
        vals.append(float(row["score_squeeze"]))
    if not vals:
        return None
    return _clip(float(np.mean(vals)))


def score_news_secteur(row: pd.Series) -> Optional[float]:
    sn = row.get("score_news")
    if pd.notna(sn):
        return _clip(float(sn))
    return None


def score_revisions(row: pd.Series) -> Optional[float]:
    """Révisions analystes 8 % – eps_revision, beat_rate, delta target."""
    vals = []
    if pd.notna(row.get("eps_revision_3m")):
        rev = float(row["eps_revision_3m"])
        vals.append(_clip(50 + rev * 3, 0, 100))
    if pd.notna(row.get("beat_rate")):
        vals.append(float(row["beat_rate"]))
    if not vals:
        return None
    return _clip(float(np.mean(vals)))


def score_risque_liquidite(row: pd.Series) -> Optional[float]:
    """Risque/Liquidité 6 % – plus haut = meilleure qualité (moins de risque)."""
    vals = []
    adv = row.get("avg_dollar_volume_20d")
    if pd.notna(adv):
        # log scale
        vals.append(_clip(np.log10(max(float(adv), 1)) / 7 * 100, 0, 100))
    capi = row.get("market_cap_m")
    if pd.notna(capi):
        c = float(capi)
        if c >= 5000:
            vals.append(85.0)
        elif c >= 500:
            vals.append(65.0)
        elif c >= 100:
            vals.append(45.0)
        else:
            vals.append(25.0)
    if pd.notna(row.get("atr_pct")):
        # volatilité extrême = risque
        atr = float(row["atr_pct"])
        vals.append(_clip(100 - atr * 800, 10, 95))
    if not vals:
        return None
    return _clip(float(np.mean(vals)))


def score_valorisation(row: pd.Series) -> Optional[float]:
    sv = row.get("score_valo")
    if pd.notna(sv):
        return _clip(float(sv))
    return None


def score_corporate_support(row: pd.Series) -> Optional[float]:
    sc = row.get("score_corporate")
    if pd.notna(sc):
        return _clip(float(sc))
    return None


def score_macro_rotation(row: pd.Series) -> Optional[float]:
    sm = row.get("score_regime")
    if pd.isna(sm):
        sm = row.get("score_macro")
    if pd.notna(sm):
        return _clip(float(sm))
    return None


PILLAR_FUNCS = {
    "technique": score_technique,
    "catalyseurs": score_catalyseurs,
    "volume_flow_squeeze": score_volume_flow_squeeze,
    "news_secteur": score_news_secteur,
    "revisions": score_revisions,
    "risque_liquidite": score_risque_liquidite,
    "valorisation": score_valorisation,
    "corporate_support": score_corporate_support,
    "macro_rotation": score_macro_rotation,
}


def apply_freshness(score: Optional[float], status: str = "FRESH") -> float:
    if score is None:
        return 0.0
    mult = FRESHNESS_MULT.get(status, 0.75)
    return float(score) * mult


def compute_score_v21_3(
    row: pd.Series,
    freshness: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Calcule le score V21.3.
    Pilier manquant → contribution 0 (poids non redistribué).
    """
    freshness = freshness or {}
    details = {}
    raw = 0.0

    for pillar, weight in WEIGHTS_V21_3.items():
        fn = PILLAR_FUNCS[pillar]
        s = fn(row)
        status = freshness.get(pillar, "FRESH")
        effective = apply_freshness(s, status) if s is not None else 0.0
        contrib = weight * effective
        raw += contrib
        details[pillar] = {
            "score": None if s is None else round(float(s), 2),
            "freshness": status,
            "effective": round(effective, 2),
            "weight": weight,
            "contribution": round(contrib, 3),
            "missing": s is None,
        }

    score_raw = _clip(raw)
    return {
        "score_v21_3": round(score_raw, 2),
        "pillars": details,
        "n_pillars_present": sum(1 for d in details.values() if not d["missing"]),
        "coverage": round(sum(1 for d in details.values() if not d["missing"]) / 9.0, 3),
    }
