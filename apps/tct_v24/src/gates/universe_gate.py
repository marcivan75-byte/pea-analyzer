"""Universe Gate V21.3 – identité PEA / ISIN / ticker."""
from __future__ import annotations

import re
from typing import Any, Dict

import numpy as np
import pandas as pd

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _pea_status(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "UNKNOWN"
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "oui", "eligible", "éligible", "pass"}:
        return "PASS"
    if text in {"false", "0", "no", "non", "ineligible", "inéligible", "fail"}:
        return "FAIL"
    try:
        num = float(value)
        if not np.isfinite(num):
            return "UNKNOWN"
        return "PASS" if num >= 0.5 else "FAIL"
    except (TypeError, ValueError):
        return "UNKNOWN"


def check_universe_row(row: pd.Series) -> Dict[str, Any]:
    reasons = []
    status = "PASS"

    isin = _clean_text(row.get("isin")).upper()
    if not ISIN_RE.fullmatch(isin):
        reasons.append("ISIN_INVALID")
        status = "REJECT"

    ticker = _clean_text(row.get("ticker")) or _clean_text(row.get("symbol"))
    if not ticker:
        reasons.append("TICKER_MISSING")
        if status != "REJECT":
            status = "QUARANTINE"

    pea_value = row.get("pea_proof_level")
    if pea_value is None or (isinstance(pea_value, float) and np.isnan(pea_value)):
        pea_value = row.get("pea_eligible")
    pea = _pea_status(pea_value)
    if pea == "FAIL":
        reasons.append("PEA_NOT_ELIGIBLE")
        status = "REJECT"
    elif pea == "UNKNOWN" and status != "REJECT":
        reasons.append("PEA_PROOF_MISSING")
        status = "QUARANTINE"

    return {"universe_status": status, "universe_reasons": reasons}


def apply_universe_gate(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    results = df.apply(check_universe_row, axis=1, result_type="expand")
    out = df.copy()
    out["universe_status"] = results["universe_status"]
    out["universe_reasons"] = results["universe_reasons"]
    return out
