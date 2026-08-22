from __future__ import annotations

from pathlib import Path
import json
import math
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
STATE_RELATIVE_PATH = Path("state/provenance/V21_8_ENTRY_EXIT_STATE.csv")
STATE_KEY_FIELDS = ("asset_class", "horizon", "isin")
SOURCE_GATED_HORIZONS = {"TCT", "CT", "MT"}


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
    return str(value).strip().lower() in {"1", "true", "yes", "y", "oui", "pass", "confirmed"}


def _first_num(row: pd.Series, fields: tuple[str, ...]):
    for field in fields:
        if field in row.index:
            value = _num(row.get(field))
            if value is not None:
                return value, field
    return None, None


def _horizon(row: pd.Series) -> str:
    return str(row.get("horizon", row.get("primary_horizon", "")) or "").upper()


def _state_key(row: pd.Series | dict) -> tuple[str, str, str]:
    getter = row.get
    return (
        str(getter("asset_class", "") or "").upper(),
        str(getter("horizon", getter("primary_horizon", "")) or "").upper(),
        str(getter("isin", "") or "").strip(),
    )


def _load_temporal_state(path: Path) -> dict[tuple[str, str, str], str]:
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return {}
    required = {*STATE_KEY_FIELDS, "v21_8_position_state"}
    if not required.issubset(frame.columns):
        return {}
    out: dict[tuple[str, str, str], str] = {}
    for _, row in frame.iterrows():
        key = _state_key(row)
        state = str(row.get("v21_8_position_state", "") or "").upper()
        if all(key) and state:
            out[key] = state
    return out


def _attach_temporal_state(decisions: pd.DataFrame, state: dict[tuple[str, str, str], str]) -> pd.DataFrame:
    out = decisions.copy()
    existing = out.get("previous_v21_8_position_state")
    previous: list[str | None] = []
    for idx, row in out.iterrows():
        explicit = None
        if existing is not None:
            value = existing.loc[idx]
            if pd.notna(value) and str(value).strip():
                explicit = str(value).strip().upper()
        previous.append(explicit or state.get(_state_key(row)))
    out["previous_v21_8_position_state"] = previous
    return out


def _persist_temporal_state(governed: pd.DataFrame, path: Path) -> int:
    rows: list[dict] = []
    for _, row in governed.iterrows():
        key = _state_key(row)
        if not all(key):
            continue
        rows.append({
            "asset_class": key[0],
            "horizon": key[1],
            "isin": key[2],
            "v21_8_position_state": str(row.get("v21_8_position_state", "") or "").upper(),
        })
    state = pd.DataFrame(rows).drop_duplicates(list(STATE_KEY_FIELDS), keep="last") if rows else pd.DataFrame(columns=[*STATE_KEY_FIELDS, "v21_8_position_state"])
    path.parent.mkdir(parents=True, exist_ok=True)
    state.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    return int(len(state))


def _tct_t2_confirmed(row: pd.Series) -> bool:
    if _bool(row.get("t2_confirmed")):
        return True
    setup = str(row.get("tct_setup", row.get("setup", "")) or "").upper()
    return setup in {"T2", "T2_CONFIRMATION", "T2_EXACT_TIMING_CONFIRMATION"}


def _source_gate_wait_reason(row: pd.Series) -> str | None:
    """Return a WAIT reason when a selected TCT/CT/MT row lacks full source confirmation.

    The source layer remains strictly post-selection: it can delay an entry but
    never create, upgrade or downgrade the model's selection decision.
    """
    hz = _horizon(row)
    asset = str(row.get("asset_class", "") or "").upper()
    if hz not in SOURCE_GATED_HORIZONS or asset not in {"ACTION", "ETF"}:
        return None
    state = str(row.get("source_validation_state", "") or "").upper()
    if state == "FULLY_VALIDATED":
        return None
    expected = str(row.get("investing_required_timeframe", "") or "").upper()
    signal = str(row.get("investing_horizon_signal", "") or "").upper()
    if state == "TIMING_WAIT":
        return f"INVESTING_{expected or hz}_{signal or 'MISSING'}_NOT_STRONG_BUY"
    if state == "BOURSORAMA_INCOMPLETE":
        return "BOURSORAMA_PRIORITY_CONTEXT_INCOMPLETE"
    if state in {"SOURCES_INCOMPLETE", "", "NOT_APPLICABLE"}:
        return "SOURCE_CONFIRMATION_REQUIRED_BEFORE_ENTRY"
    return f"SOURCE_CONFIRMATION_{state}_BEFORE_ENTRY"


def classify_entry(row: pd.Series, cfg: dict) -> tuple[str, list[str]]:
    """Keep selection and entry separate; source/timing evidence fails closed to WAIT."""
    selected = {str(x).upper() for x in cfg["entry_gate"].get("selected_decision_codes", ["BUY", "ACTION", "BUY_CANDIDATE"])}
    decision = str(row.get("decision", "") or "").upper()
    if decision not in selected:
        return "TEMPORARY_REJECT", ["NOT_SELECTED_FOR_ENTRY"]

    source_wait = _source_gate_wait_reason(row)
    if source_wait:
        return "WAIT", [source_wait]

    hz = _horizon(row)
    if hz == "TCT" and cfg["entry_gate"].get("tct_requires_exact_t2_confirmation", True):
        if not _tct_t2_confirmed(row):
            return "WAIT", ["TCT_EXACT_T2_CONFIRMATION_REQUIRED"]

    score, _ = _first_num(row, ("score_final", "score", "Score"))
    dist50, _ = _first_num(row, ("dist_sma50", "distance_sma50"))
    dist200, _ = _first_num(row, ("dist_sma200", "distance_sma200"))
    accel, _ = _first_num(row, ("momentum_accel", "momentum_acceleration"))
    vol20, _ = _first_num(row, ("vol20", "volatility_20d"))

    if all(v is None for v in (dist50, dist200, accel, vol20)):
        return "WAIT", ["ENTRY_TIMING_EVIDENCE_MISSING"]

    reasons: list[str] = []
    if score is not None and score >= float(cfg["entry_gate"].get("extreme_score_reference", 92.0)):
        reasons.append("EXTREME_SCORE_REQUIRES_OVEREXTENSION_REVIEW")
    if dist50 is not None and dist50 > 0:
        reasons.append("ABOVE_SMA50_CHECK_EXTENSION")
    if accel is not None and accel < 0:
        reasons.append("MOMENTUM_DECELERATION")
    if dist200 is not None and dist200 < 0:
        reasons.append("BELOW_SMA200_TREND_CONCERN")

    blocking = {"MOMENTUM_DECELERATION", "BELOW_SMA200_TREND_CONCERN", "EXTREME_SCORE_REQUIRES_OVEREXTENSION_REVIEW"}
    if blocking.intersection(reasons):
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
    if dist50 is not None and dist50 < 0: reasons.append("BELOW_SMA50")
    if dist200 is not None and dist200 < 0: reasons.append("BELOW_SMA200")
    if slope50 is not None and slope50 < 0: reasons.append("SMA50_SLOPE_NEGATIVE")
    if ret21 is not None and ret21 < 0: reasons.append("RETURN_21D_NEGATIVE")
    elif accel is not None and accel < 0: reasons.append("MOMENTUM_DECELERATION")
    if market21 is not None and market200 is not None and market21 < 0 and market200 < 0: reasons.append("MARKET_REGIME_DETERIORATED")
    return reasons


def classify_position(row: pd.Series, cfg: dict) -> tuple[str, list[str]]:
    """Two-stage decision support: deterioration -> PROTECT -> confirmed multifactor EXIT."""
    if _bool(row.get("emergency_risk_flag")):
        return "EMERGENCY_EXIT", ["EXPLICIT_EMERGENCY_RISK_FLAG"]
    reasons = _deterioration_reasons(row)
    pnl, _ = _first_num(row, ("return_since_entry", "pnl_pct", "performance_pct", "return_pct"))
    peak, _ = _first_num(row, ("max_return_since_entry", "peak_return_since_entry", "mfe_since_entry"))
    if pnl is not None and peak is not None and peak > pnl: reasons.append("PROFIT_GIVEBACK_OBSERVED_CONTEXT_ONLY")
    structural = {"BELOW_SMA200", "SMA50_SLOPE_NEGATIVE", "BELOW_SMA50"}; momentum = {"RETURN_21D_NEGATIVE", "MOMENTUM_DECELERATION"}; structural_count = len(structural.intersection(reasons)); momentum_count = len(momentum.intersection(reasons)); market_bad = "MARKET_REGIME_DETERIORATED" in reasons; multifactor = structural_count >= 1 and momentum_count >= 1 and (structural_count >= 2 or market_bad)
    previous = str(row.get("previous_v21_8_position_state", row.get("previous_position_state", "")) or "").upper(); confirmed = _bool(row.get("deterioration_confirmed")) or previous in {"PROTECT", "EXIT"}
    if multifactor and confirmed: return "EXIT", reasons + ["MULTIFACTOR_DETERIORATION_CONFIRMED_AFTER_PROTECT"]
    if reasons and any(r != "PROFIT_GIVEBACK_OBSERVED_CONTEXT_ONLY" for r in reasons): return "PROTECT", reasons + (["AWAIT_TEMPORAL_CONFIRMATION"] if multifactor else [])
    if pnl is not None and pnl > 0: return "HOLD", ["POSITIVE_POSITION_NO_VALIDATED_EXIT_TRIGGER"]
    return "HOLD", ["NO_VALIDATED_EXIT_TRIGGER"]


def apply_governance(decisions: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = decisions.copy(); score_guard = out["score"].copy() if "score" in out.columns else None; decision_guard = out["decision"].copy() if "decision" in out.columns else None
    entry_states, entry_reasons, position_states, position_reasons = [], [], [], []
    for _, row in out.iterrows():
        e_state, e_reasons = classify_entry(row, cfg); p_state, p_reasons = classify_position(row, cfg); entry_states.append(e_state); entry_reasons.append("|".join(e_reasons)); position_states.append(p_state); position_reasons.append("|".join(p_reasons))
    out["v21_8_entry_state"] = entry_states; out["v21_8_entry_reasons"] = entry_reasons; out["v21_8_position_state"] = position_states; out["v21_8_position_reasons"] = position_reasons
    out["v21_8_fixed_take_profit"] = False; out["v21_8_legacy_fixed_stop_engine"] = False; out["v21_8_new_hard_stop_promoted"] = False; out["v21_8_decision_influence"] = 0.0; out["v21_8_score_influence"] = 0.0; out["v21_8_sizing_influence"] = 0.0; out["v21_8_real_order"] = False
    if score_guard is not None and not score_guard.reset_index(drop=True).equals(out["score"].reset_index(drop=True)): raise RuntimeError("V21_8_SCORE_MUTATION_FORBIDDEN")
    if decision_guard is not None and not decision_guard.reset_index(drop=True).equals(out["decision"].reset_index(drop=True)): raise RuntimeError("V21_8_DECISION_MUTATION_FORBIDDEN")
    return out


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root/"config"/"V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8")); src = root/"outputs"/"committee_master"/"COMMITTEE_DECISIONS.csv"
    if not src.exists(): return {"status": "BLOCKED_COMMITTEE_DECISIONS_MISSING", "decision_influence": 0.0, "real_orders_enabled": False}
    decisions = pd.read_csv(src, sep=";", encoding="utf-8-sig", low_memory=False); state_path = root/STATE_RELATIVE_PATH; previous_state = _load_temporal_state(state_path); governed = apply_governance(_attach_temporal_state(decisions, previous_state), cfg); state_rows = _persist_temporal_state(governed, state_path)
    outdir = root/"outputs"/"committee_master"; auditdir = root/"outputs"/"audit"; auditdir.mkdir(parents=True, exist_ok=True); governed.to_csv(outdir/"V21_8_ENTRY_EXIT_CHALLENGER.csv", sep=";", index=False, encoding="utf-8-sig")
    source_waits = int(governed.get("v21_8_entry_reasons", pd.Series(dtype=str)).astype(str).str.contains("SOURCE_|INVESTING_|BOURSORAMA_", regex=True).sum())
    payload = {"status": "SUCCESS", "version": cfg["version"], "mode": cfg["mode"], "rows": int(len(governed)), "entry_states": governed["v21_8_entry_state"].value_counts(dropna=False).to_dict(), "position_states": governed["v21_8_position_state"].value_counts(dropna=False).to_dict(), "source_confirmation_entry_waits": source_waits, "source_confirmation_required_for_tct_ct_mt": True, "fixed_take_profit_enabled": False, "legacy_fixed_stop_engine_enabled": False, "historical_plus_4pct_operational": False, "historical_minus_18pct_etf_operational": False, "new_hard_stop_promoted": False, "desired_loss_risk_ceiling_pct_for_research": cfg["risk"]["desired_loss_risk_ceiling_pct_for_research"], "desired_loss_risk_ceiling_is_blind_stop": False, "tct_requires_exact_t2_confirmation": True, "t1_t2_scope": "ACTION_TCT_ONLY", "exit_requires_temporal_confirmation": True, "temporal_state_persisted": True, "temporal_state_path": str(STATE_RELATIVE_PATH), "temporal_state_rows": state_rows, "weights_unchanged": True, "selection_threshold_unchanged": True, "decision_influence": 0.0, "score_influence": 0.0, "sizing_influence": 0.0, "holdout_opened": False, "real_orders_enabled": False}
    (auditdir/"V21_8_ENTRY_EXIT_GOVERNANCE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); print(json.dumps(payload, ensure_ascii=False, indent=2)); return payload


if __name__ == "__main__": run()
