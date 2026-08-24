from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
STATE_RELATIVE_PATH = Path("state/provenance/V21_8_ENTRY_EXIT_STATE.csv")
STATE_KEY_FIELDS = ("asset_class", "horizon", "isin")
STATE_OBSERVED_AT_FIELD = "v21_8_observed_at_utc"
TRADINGVIEW_STATES = {"STRONG_SELL", "SELL", "NEUTRAL", "BUY", "STRONG_BUY"}


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


def _tradingview_signal(row: pd.Series) -> str | None:
    direct = str(row.get("tradingview_horizon_signal", "") or "").strip().upper()
    if direct in TRADINGVIEW_STATES:
        return direct
    field = {"TCT": "tradingview_daily_signal", "CT": "tradingview_weekly_signal", "MT": "tradingview_monthly_signal"}.get(_horizon(row))
    if not field:
        return None
    signal = str(row.get(field, "") or "").strip().upper()
    return signal if signal in TRADINGVIEW_STATES else None


def _state_key(row: pd.Series | dict) -> tuple[str, str, str]:
    getter = row.get
    return (
        str(getter("asset_class", "") or "").upper(),
        str(getter("horizon", getter("primary_horizon", "")) or "").upper(),
        str(getter("isin", "") or "").strip(),
    )


def _load_state_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def _load_temporal_state(path: Path) -> dict[tuple[str, str, str], str]:
    frame = _load_state_frame(path)
    required = {*STATE_KEY_FIELDS, "v21_8_position_state"}
    if frame.empty or not required.issubset(frame.columns):
        return {}
    out = {}
    for _, row in frame.iterrows():
        key = _state_key(row)
        state = str(row.get("v21_8_position_state", "") or "").upper()
        if all(key) and state:
            out[key] = state
    return out


def _load_temporal_state_observed_at(path: Path) -> dict[tuple[str, str, str], str]:
    frame = _load_state_frame(path)
    required = {*STATE_KEY_FIELDS, STATE_OBSERVED_AT_FIELD}
    if frame.empty or not required.issubset(frame.columns):
        return {}
    out = {}
    for _, row in frame.iterrows():
        key = _state_key(row)
        value = str(row.get(STATE_OBSERVED_AT_FIELD, "") or "").strip()
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
        if all(key) and value and pd.notna(parsed):
            out[key] = parsed.isoformat()
    return out


def _attach_temporal_state(decisions: pd.DataFrame, state: dict, state_observed_at: dict | None = None) -> pd.DataFrame:
    out = decisions.copy()
    existing = out.get("previous_v21_8_position_state")
    existing_observed = out.get("previous_v21_8_observed_at_utc")
    observed_map = state_observed_at or {}
    previous, previous_observed = [], []
    for idx, row in out.iterrows():
        key = _state_key(row)
        explicit = None
        if existing is not None:
            value = existing.loc[idx]
            if pd.notna(value) and str(value).strip():
                explicit = str(value).strip().upper()
        previous.append(explicit or state.get(key))
        explicit_observed = None
        if existing_observed is not None:
            value = existing_observed.loc[idx]
            parsed = pd.to_datetime(value, errors="coerce", utc=True)
            if pd.notna(parsed):
                explicit_observed = parsed.isoformat()
        previous_observed.append(explicit_observed or observed_map.get(key))
    out["previous_v21_8_position_state"] = previous
    out["previous_v21_8_observed_at_utc"] = previous_observed
    return out


def _current_observed_at(row: pd.Series) -> str:
    for field in ("generated_at_utc", "snapshot_at_utc", "as_of_utc"):
        parsed = pd.to_datetime(row.get(field), errors="coerce", utc=True)
        if pd.notna(parsed):
            return parsed.isoformat()
    return datetime.now(timezone.utc).isoformat()


def _persist_temporal_state(governed: pd.DataFrame, path: Path) -> int:
    rows = []
    for _, row in governed.iterrows():
        key = _state_key(row)
        if all(key):
            rows.append({"asset_class": key[0], "horizon": key[1], "isin": key[2], "v21_8_position_state": str(row.get("v21_8_position_state", "") or "").upper(), STATE_OBSERVED_AT_FIELD: _current_observed_at(row)})
    columns = [*STATE_KEY_FIELDS, "v21_8_position_state", STATE_OBSERVED_AT_FIELD]
    state = pd.DataFrame(rows).drop_duplicates(list(STATE_KEY_FIELDS), keep="last") if rows else pd.DataFrame(columns=columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    return int(len(state))


def _tct_t2_confirmed(row: pd.Series) -> bool:
    if _bool(row.get("t2_confirmed")):
        return True
    setup = str(row.get("tct_setup", row.get("setup", "")) or "").upper()
    return setup in {"T2", "T2_CONFIRMATION", "T2_EXACT_TIMING_CONFIRMATION"}


def classify_entry(row: pd.Series, cfg: dict) -> tuple[str, list[str]]:
    selected = {str(x).upper() for x in cfg["entry_gate"].get("selected_decision_codes", ["BUY", "ACTION", "BUY_CANDIDATE"])}
    decision = str(row.get("decision", "") or "").upper()
    if decision not in selected:
        return "TEMPORARY_REJECT", ["NOT_SELECTED_FOR_ENTRY"]
    if _horizon(row) == "TCT" and cfg["entry_gate"].get("tct_requires_exact_t2_confirmation", True) and not _tct_t2_confirmed(row):
        return "WAIT", ["TCT_EXACT_T2_CONFIRMATION_REQUIRED"]

    score, _ = _first_num(row, ("score_final", "score", "Score"))
    dist50, _ = _first_num(row, ("dist_sma50", "distance_sma50"))
    dist200, _ = _first_num(row, ("dist_sma200", "distance_sma200"))
    accel, _ = _first_num(row, ("momentum_accel", "momentum_acceleration"))
    vol20, _ = _first_num(row, ("vol20", "volatility_20d"))
    if all(v is None for v in (dist50, dist200, accel, vol20)):
        return "WAIT", ["ENTRY_TIMING_EVIDENCE_MISSING"]

    reasons = []
    if score is not None and score >= float(cfg["entry_gate"].get("extreme_score_reference", 92.0)):
        reasons.append("EXTREME_SCORE_REQUIRES_OVEREXTENSION_REVIEW")
    if dist50 is not None and dist50 > 0:
        reasons.append("ABOVE_SMA50_CHECK_EXTENSION")
    if accel is not None and accel < 0:
        reasons.append("MOMENTUM_DECELERATION")
    if dist200 is not None and dist200 < 0:
        reasons.append("BELOW_SMA200_TREND_CONCERN")

    tcfg = cfg.get("tradingview_confirmation", {})
    signal = _tradingview_signal(row) if tcfg.get("enabled", False) else None
    if signal in set(tcfg.get("entry_blocking_states", ["SELL", "STRONG_SELL"])):
        reasons.append(f"TRADINGVIEW_{signal}_ENTRY_BLOCK")
    elif signal in set(tcfg.get("entry_wait_states", ["NEUTRAL"])):
        reasons.append("TRADINGVIEW_NEUTRAL_ENTRY_NOT_CONFIRMED")
    elif signal in set(tcfg.get("entry_confirming_states", ["BUY", "STRONG_BUY"])):
        reasons.append(f"TRADINGVIEW_{signal}_ENTRY_CONFIRMED")
    elif tcfg.get("enabled", False):
        reasons.append("TRADINGVIEW_SIGNAL_MISSING_FALLBACK_INTERNAL")

    blocking = {"MOMENTUM_DECELERATION", "BELOW_SMA200_TREND_CONCERN", "EXTREME_SCORE_REQUIRES_OVEREXTENSION_REVIEW", "TRADINGVIEW_SELL_ENTRY_BLOCK", "TRADINGVIEW_STRONG_SELL_ENTRY_BLOCK", "TRADINGVIEW_NEUTRAL_ENTRY_NOT_CONFIRMED"}
    if blocking.intersection(reasons):
        return "WAIT", reasons
    return "ACTION", reasons or ["NO_CHALLENGER_TIMING_BLOCKER_OBSERVED"]


def _deterioration_reasons(row: pd.Series, cfg: dict | None = None) -> list[str]:
    reasons = []
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
    tcfg = (cfg or {}).get("tradingview_confirmation", {})
    if tcfg.get("enabled", False):
        signal = _tradingview_signal(row)
        if signal == "STRONG_SELL" and tcfg.get("strong_sell_is_major_deterioration", True): reasons.append("TRADINGVIEW_STRONG_SELL_MAJOR_DETERIORATION")
        elif signal == "SELL" and tcfg.get("sell_is_position_deterioration", True): reasons.append("TRADINGVIEW_SELL_DETERIORATION")
    return reasons


def _previous_protect_is_temporally_confirmed(row: pd.Series) -> bool:
    if "previous_v21_8_observed_at_utc" not in row.index:
        return True
    previous = pd.to_datetime(row.get("previous_v21_8_observed_at_utc"), errors="coerce", utc=True)
    current = pd.to_datetime(row.get("generated_at_utc"), errors="coerce", utc=True)
    if pd.isna(previous) or pd.isna(current):
        return False
    return previous.date() < current.date()


def classify_position(row: pd.Series, cfg: dict) -> tuple[str, list[str]]:
    if _bool(row.get("emergency_risk_flag")):
        return "EMERGENCY_EXIT", ["EXPLICIT_EMERGENCY_RISK_FLAG"]
    reasons = _deterioration_reasons(row, cfg)
    pnl, _ = _first_num(row, ("return_since_entry", "pnl_pct", "performance_pct", "return_pct"))
    peak, _ = _first_num(row, ("max_return_since_entry", "peak_return_since_entry", "mfe_since_entry"))
    if pnl is not None and peak is not None and peak > pnl:
        reasons.append("PROFIT_GIVEBACK_OBSERVED_CONTEXT_ONLY")
    structural = {"BELOW_SMA200", "SMA50_SLOPE_NEGATIVE", "BELOW_SMA50"}
    momentum = {"RETURN_21D_NEGATIVE", "MOMENTUM_DECELERATION", "TRADINGVIEW_SELL_DETERIORATION", "TRADINGVIEW_STRONG_SELL_MAJOR_DETERIORATION"}
    structural_count = len(structural.intersection(reasons)); momentum_count = len(momentum.intersection(reasons))
    market_bad = "MARKET_REGIME_DETERIORATED" in reasons
    tv_strong_sell = "TRADINGVIEW_STRONG_SELL_MAJOR_DETERIORATION" in reasons
    multifactor = structural_count >= 1 and momentum_count >= 1 and (structural_count >= 2 or market_bad or tv_strong_sell)
    previous = str(row.get("previous_v21_8_position_state", row.get("previous_position_state", "")) or "").upper()
    explicit_confirmation = _bool(row.get("deterioration_confirmed"))
    prior_confirmation = previous == "EXIT" or (previous == "PROTECT" and _previous_protect_is_temporally_confirmed(row))
    confirmed = explicit_confirmation or prior_confirmation
    if multifactor and confirmed:
        return "EXIT", reasons + ["MULTIFACTOR_DETERIORATION_CONFIRMED_AFTER_PROTECT"]
    if reasons and any(r != "PROFIT_GIVEBACK_OBSERVED_CONTEXT_ONLY" for r in reasons):
        suffix = ["AWAIT_TEMPORAL_CONFIRMATION"] if multifactor else []
        if multifactor and previous == "PROTECT" and not confirmed: suffix.append("SAME_DAY_RERUN_NOT_TEMPORAL_CONFIRMATION")
        return "PROTECT", reasons + suffix
    signal = _tradingview_signal(row)
    if signal in {"BUY", "STRONG_BUY"}:
        return "HOLD", [f"TRADINGVIEW_{signal}_POSITION_SUPPORT"]
    if pnl is not None and pnl > 0:
        return "HOLD", ["POSITIVE_POSITION_NO_VALIDATED_EXIT_TRIGGER"]
    return "HOLD", ["NO_VALIDATED_EXIT_TRIGGER"]


def apply_governance(decisions: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = decisions.copy()
    score_guard = out["score"].copy() if "score" in out.columns else None
    decision_guard = out["decision"].copy() if "decision" in out.columns else None
    entry_states, entry_reasons, position_states, position_reasons, signals = [], [], [], [], []
    for _, row in out.iterrows():
        e_state, e_reasons = classify_entry(row, cfg); p_state, p_reasons = classify_position(row, cfg)
        entry_states.append(e_state); entry_reasons.append("|".join(e_reasons)); position_states.append(p_state); position_reasons.append("|".join(p_reasons)); signals.append(_tradingview_signal(row))
    out["v21_8_entry_state"] = entry_states
    out["v21_8_entry_reasons"] = entry_reasons
    out["v21_8_position_state"] = position_states
    out["v21_8_position_reasons"] = position_reasons
    out["v21_8_tradingview_horizon_signal"] = signals
    out["v21_8_tradingview_entry_exit_influence"] = [signal is not None for signal in signals]
    out["v21_8_fixed_take_profit"] = False; out["v21_8_legacy_fixed_stop_engine"] = False; out["v21_8_new_hard_stop_promoted"] = False
    out["v21_8_decision_influence"] = 0.0; out["v21_8_score_influence"] = 0.0; out["v21_8_sizing_influence"] = 0.0; out["v21_8_real_order"] = False
    if score_guard is not None and not score_guard.reset_index(drop=True).equals(out["score"].reset_index(drop=True)): raise RuntimeError("V21_8_SCORE_MUTATION_FORBIDDEN")
    if decision_guard is not None and not decision_guard.reset_index(drop=True).equals(out["decision"].reset_index(drop=True)): raise RuntimeError("V21_8_DECISION_MUTATION_FORBIDDEN")
    return out


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / "config/V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8"))
    src = root / "outputs/committee_master/COMMITTEE_DECISIONS.csv"
    if not src.exists():
        return {"status":"BLOCKED_COMMITTEE_DECISIONS_MISSING","decision_influence":0.0,"real_orders_enabled":False}
    decisions = pd.read_csv(src, sep=";", encoding="utf-8-sig", low_memory=False)
    state_path = root / STATE_RELATIVE_PATH
    governed = apply_governance(_attach_temporal_state(decisions, _load_temporal_state(state_path), _load_temporal_state_observed_at(state_path)), cfg)
    state_rows = _persist_temporal_state(governed, state_path)
    outdir=root/"outputs/committee_master"; auditdir=root/"outputs/audit"; auditdir.mkdir(parents=True,exist_ok=True)
    governed.to_csv(outdir/"V21_8_ENTRY_EXIT_CHALLENGER.csv",sep=";",index=False,encoding="utf-8-sig")
    available=int(governed["v21_8_tradingview_horizon_signal"].notna().sum())
    payload={"status":"SUCCESS","version":cfg["version"],"mode":cfg["mode"],"rows":len(governed),"entry_states":governed["v21_8_entry_state"].value_counts(dropna=False).to_dict(),"position_states":governed["v21_8_position_state"].value_counts(dropna=False).to_dict(),"technical_provider":"TradingView","tradingview_confirmation_enabled":bool(cfg.get("tradingview_confirmation",{}).get("enabled",False)),"tradingview_signals_available":available,"tradingview_horizon_mapping":cfg.get("tradingview_confirmation",{}).get("horizon_mapping",{}),"tradingview_changes_selection_score":False,"tradingview_can_create_buy_candidate":False,"tradingview_strong_sell_can_exit_alone":False,"fixed_take_profit_enabled":False,"legacy_fixed_stop_engine_enabled":False,"new_hard_stop_promoted":False,"tct_requires_exact_t2_confirmation":True,"t1_t2_scope":"ACTION_TCT_ONLY","exit_requires_temporal_confirmation":True,"same_day_rerun_can_confirm_exit":False,"temporal_state_persisted":True,"temporal_state_rows":state_rows,"weights_unchanged":True,"selection_threshold_unchanged":True,"decision_influence":0.0,"score_influence":0.0,"sizing_influence":0.0,"real_orders_enabled":False,"investing_active":False}
    (auditdir/"V21_8_ENTRY_EXIT_GOVERNANCE.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    return payload


if __name__ == "__main__": print(json.dumps(run(),ensure_ascii=False,indent=2))
