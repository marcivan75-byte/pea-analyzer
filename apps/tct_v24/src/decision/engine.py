"""Decision Engine V21.3 – REJECT / SCAN / SATELLITE / COEUR."""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

THRESHOLDS = {"COEUR": 72.0, "SATELLITE": 58.0, "SCAN": 45.0}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if np.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def classify_row(row: pd.Series) -> Dict[str, Any]:
    status = str(row.get("universe_status") or "")
    if bool(row.get("has_veto")) or status == "REJECT":
        return {"decision_v21": "REJECT", "decision_reason": "VETO_OR_UNIVERSE"}
    if status == "QUARANTINE":
        return {"decision_v21": "REJECT", "decision_reason": "UNIVERSE_QUARANTINE"}

    # Le moteur unifié ne doit pas transformer en opportunité un titre explicitement
    # ignoré par le sizing V24 (gap, liquidité, meta, J-1, etc.).
    if str(row.get("decision") or "").upper() == "IGNORE":
        reason = str(row.get("sizing_reason") or "V24_IGNORE")
        return {"decision_v21": "REJECT", "decision_reason": f"V24_IGNORE:{reason}"}

    score = _num(row.get("score_after_synergy"), np.nan)
    if not np.isfinite(score):
        score = _num(row.get("score_v21_3"), 0.0)

    flags = row.get("flags")
    if flags is None or (isinstance(flags, float) and np.isnan(flags)):
        flags = []
    elif isinstance(flags, str):
        flags = [flags]
    elif not isinstance(flags, (list, tuple, set)):
        flags = []

    coeur_ok = score >= THRESHOLDS["COEUR"]
    if coeur_ok:
        strong = any(f in flags for f in (
            "T2_CONFIRM", "SYNERGY_T2_EARNINGS", "SYNERGY_SQUEEZE_VOL", "EARNINGS_STRONG"
        ))
        if not strong and score < 80:
            coeur_ok = False

    if coeur_ok:
        return {"decision_v21": "COEUR", "decision_reason": f"score={score:.1f}"}
    if score >= THRESHOLDS["SATELLITE"]:
        return {"decision_v21": "SATELLITE", "decision_reason": f"score={score:.1f}"}
    if score >= THRESHOLDS["SCAN"]:
        return {"decision_v21": "SCAN", "decision_reason": f"score={score:.1f}"}
    return {"decision_v21": "REJECT", "decision_reason": f"score_low={score:.1f}"}


def apply_decision_engine(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    res = df.apply(classify_row, axis=1, result_type="expand")
    out = df.copy()
    out["decision_v21"] = res["decision_v21"]
    out["decision_reason"] = res["decision_reason"]
    return out


def extract_top20(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    eligible = df[df["decision_v21"].isin(["COEUR", "SATELLITE", "SCAN"])].copy()
    if eligible.empty:
        return eligible
    sort_col = "score_after_synergy" if "score_after_synergy" in eligible.columns else "score_v21_3"
    eligible[sort_col] = pd.to_numeric(eligible[sort_col], errors="coerce").fillna(-np.inf)
    return eligible.nlargest(min(20, len(eligible)), sort_col)
