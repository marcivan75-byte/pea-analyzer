from __future__ import annotations

from pathlib import Path
import json
import math
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]


def _num(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "oui", "pass", "confirmed"}


def _first_num(row: pd.Series, fields: tuple[str, ...]):
    for field in fields:
        if field in row.index:
            value = _num(row.get(field))
            if value is not None:
                return value, field
    return None, None


def _horizon(row: pd.Series) -> str:
    return str(row.get("horizon", row.get("primary_horizon", "")) or "").upper()


def _tct_t2_confirmed(row: pd.Series) -> bool:
    if _bool(row.get("t2_confirmed")):
        return True
    setup = str(row.get("tct_setup", row.get("setup", "")) or "").upper()
    return setup in {"T2", "T2_CONFIRMATION", "T2_EXACT_TIMING_CONFIRMATION"}


def classify_entry(row: pd.Series, cfg: dict) -> tuple[str, list[str]]:
    """Selection and entry are separate. Missing evidence fails closed to WAIT."""
    reasons: list[str] = []
    decision = str(row.get("decision", "")).upper()
    if decision not in {"BUY", "ACTION"}:
        return "TEMPORARY_REJECT", ["NOT_SELECTED_FOR_ENTRY"]

    hz = _horizon(row)
    if hz == "TCT" and cfg["entry_gate"].get("tct_requires_exact_t2_confirmation", True):
        if not _tct_t2_confirmed(row):
            return "WAIT", ["TCT_EXACT_T2_CONFIRMATION_REQUIRED"]

    score, _ = _first_num(row, ("score_final", "score", "Score"))
    dist50, _ = _first_num(row, ("dist_sma50", "distance_sma50"))
    dist200, _ = _first_num(row, ("dist_sma200", "distance_sma200"))
    accel, _ = _first_num(row, ("momentum_accel", "momentum_acceleration"))
    vol20, _ = _first_num(row, ("vol20", "volatility_20d"))

    evidence = [dist50, dist200, accel, vol20]
    if all(v is None for v in evidence):
        return "WAIT", ["ENTRY_TIMING_EVIDENCE_MISSING"]

    if score is not None and score >= float(cfg["entry_gate"].get("extreme_score_reference", 92.0)):
        reasons.append("EXTREME_SCORE_REQUIRES_OVEREXTENSION_REVIEW")
    if dist50 is not None and dist50 > 0:
        reasons.append("ABOVE_SMA50_CHECK_EXTENSION")
    if accel is not None and accel < 0:
        reasons.append("MOMENTUM_DECELERATION")
    if dist200 is not None and dist200 < 0:
        reasons.append("BELOW_SMA200_TREND_CONCERN")

    blocking = {"MOMENTUM_DECELERATION", "BELOW_SMA200_TREND_CONCERN"}
    if blocking.intersection(reasons):
        return "WAIT", reasons
    if "EXTREME_SCORE_REQUIRES_OVEREXTENSION_REVIEW" in reasons:
        return "WAIT", reasons
    return "ACTION", reasons or ["NO_CHALLENGER_TIMING_BLOCKER_OBSERVED"]


def _deterioration_reasons(row: pd.Series) -> list[str]:
    reasons: list[str] = []
    dist50, _ = _first_num(row, ("dist_sma50", "distance_sma50"))
    dist200, _ = _first_num(row, ("dist_sma200", "distance_sma200"))
    slope50, _ = _first_num(row, ("slope_sma50_20d", "sma50_slope_20d"))
    ret21, _ = _first_num(row, ("ret_21d", "return_21d"))
    accel, _ = _first_num(row, ("momentum_accel", "momentum_acceleration"))
    market21, _ = _first_num(row, ("market_ret_21d",))
    market200, _ = _first_num(row, ("market_dist_sma200",))

    if dist50 is not None and dist50 < 0:
        reasons.append("BELOW_SMA50")
    if dist200 is not None and dist200 < 0:
        reasons.append("BELOW_SMA200")
    if slope50 is not None and slope50 < 0:
        reasons.append("SMA50_SLOPE_NEGATIVE")
    if ret21 is not None and ret21 < 0:
        reasons.append("RETURN_21D_NEGATIVE")
    elif accel is not None and accel < 0:
        reasons.append("MOMENTUM_DECELERATION")
    if market21 is not None and market200 is not None and market21 < 0 and market200 < 0:
        reasons.append("MARKET_REGIME_DETERIORATED")
    return reasons


def classify_position(row: pd.Series, cfg: dict) -> tuple[str, list[str]]:
    """Two-stage challenger: deterioration -> PROTECT -> confirmed multifactor EXIT.

    Profit level and giveback are context only. They never trigger an exit alone.
    """
    if _bool(row.get("emergency_risk_flag")):
        return "EMERGENCY_EXIT", ["EXPLICIT_EMERGENCY_RISK_FLAG"]

    reasons = _deterioration_reasons(row)
    pnl, _ = _first_num(row, ("return_since_entry", "pnl_pct", "performance_pct", "return_pct"))
    peak, _ = _first_num(row, ("max_return_since_entry", "peak_return_since_entry", "mfe_since_entry"))
    if pnl is not None and peak is not None and peak > pnl:
        reasons.append("PROFIT_GIVEBACK_OBSERVED_CONTEXT_ONLY")

    structural = {"BELOW_SMA200", "SMA50_SLOPE_NEGATIVE", "BELOW_SMA50"}
    momentum = {"RETURN_21D_NEGATIVE", "MOMENTUM_DECELERATION"}
    structural_count = len(structural.intersection(reasons))
    momentum_count = len(momentum.intersection(reasons))
    market_bad = "MARKET_REGIME_DETERIORATED" in reasons
    multifactor = structural_count >= 1 and momentum_count >= 1 and (structural_count >= 2 or market_bad)

    previous = str(row.get("previous_v21_8_position_state", row.get("previous_position_state", "")) or "").upper()
    confirmed = _bool(row.get("deterioration_confirmed")) or previous == "PROTECT"

    if multifactor and confirmed:
        return "EXIT", reasons + ["MULTIFACTOR_DETERIORATION_CONFIRMED_AFTER_PROTECT"]
    if reasons and any(r != "PROFIT_GIVEBACK_OBSERVED_CONTEXT_ONLY" for r in reasons):
        return "PROTECT", reasons + (["AWAIT_TEMPORAL_CONFIRMATION"] if multifactor else [])
    if pnl is not None and pnl > 0:
        return "HOLD", ["POSITIVE_POSITION_NO_VALIDATED_EXIT_TRIGGER"]
    return "HOLD", ["NO_VALIDATED_EXIT_TRIGGER"]


def apply_governance(decisions: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = decisions.copy()
    entry_states, entry_reasons, position_states, position_reasons = [], [], [], []
    for _, row in out.iterrows():
        e_state, e_reasons = classify_entry(row, cfg)
        p_state, p_reasons = classify_position(row, cfg)
        entry_states.append(e_state)
        entry_reasons.append("|".join(e_reasons))
        position_states.append(p_state)
        position_reasons.append("|".join(p_reasons))
    out["v21_8_entry_state"] = entry_states
    out["v21_8_entry_reasons"] = entry_reasons
    out["v21_8_position_state"] = position_states
    out["v21_8_position_reasons"] = position_reasons
    out["v21_8_fixed_take_profit"] = False
    out["v21_8_legacy_fixed_stop_engine"] = False
    out["v21_8_new_hard_stop_promoted"] = False
    out["v21_8_real_order"] = False
    return out


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / "config" / "V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8"))
    src = root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv"
    if not src.exists():
        return {"status": "BLOCKED_COMMITTEE_DECISIONS_MISSING"}
    decisions = pd.read_csv(src, sep=";", encoding="utf-8-sig", low_memory=False)
    governed = apply_governance(decisions, cfg)
    outdir = root / "outputs" / "committee_master"
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    governed.to_csv(outdir / "V21_8_ENTRY_EXIT_CHALLENGER.csv", sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "status": "SUCCESS",
        "version": cfg["version"],
        "rows": int(len(governed)),
        "entry_states": governed["v21_8_entry_state"].value_counts(dropna=False).to_dict(),
        "position_states": governed["v21_8_position_state"].value_counts(dropna=False).to_dict(),
        "fixed_take_profit_enabled": False,
        "legacy_fixed_stop_engine_enabled": False,
        "historical_plus_4pct_operational": False,
        "historical_minus_18pct_etf_operational": False,
        "new_hard_stop_promoted": False,
        "tct_requires_exact_t2_confirmation": True,
        "exit_requires_temporal_confirmation": True,
        "weights_unchanged": True,
        "holdout_opened": False,
        "real_orders_enabled": False,
    }
    (auditdir / "V21_8_ENTRY_EXIT_GOVERNANCE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
