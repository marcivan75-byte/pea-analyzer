from __future__ import annotations

import math
from typing import Any

import pandas as pd

APPLICABLE_HORIZONS = {"TCT", "CT", "MT"}
ACTION_CONTEXT_FIELDS = (
    "boursorama_consensus", "boursorama_n_analysts", "boursorama_target_median", "boursorama_target_upside_pct",
    "boursorama_estimated_per", "boursorama_estimated_yield_pct", "boursorama_perf_1w_pct", "boursorama_perf_1m_pct",
    "boursorama_perf_6m_pct", "boursorama_perf_1y_pct", "boursorama_operating_margin_pct", "boursorama_roe_pct",
)
ETF_CONTEXT_FIELDS = (
    "boursorama_etf_aum_eur_m", "boursorama_etf_morningstar_category", "boursorama_etf_replication",
    "boursorama_etf_management_fee_pct", "boursorama_etf_asset_class", "boursorama_etf_geographic_zone",
    "boursorama_etf_volatility_1y_pct", "boursorama_etf_beta_1y",
)


def _present(value: Any) -> bool:
    if value is None: return False
    try:
        if bool(pd.isna(value)): return False
    except (TypeError, ValueError): pass
    return not (isinstance(value, str) and value.strip().lower() in {"", "nan", "none", "null", "n/a", "na", "<na>"})


def _number(value: Any) -> float | None:
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if math.isfinite(result) else None


def _fresh(age: Any, maximum: float) -> bool:
    parsed = _number(age); return parsed is not None and 0.0 <= parsed <= float(maximum)


def _action_boursorama_ready(row: pd.Series, cfg: dict) -> tuple[bool, list[str]]:
    rule = cfg["boursorama"]["action_ready_rule"]; missing_all = [field for field in rule.get("all", []) if not _present(row.get(field))]; any_fields = list(rule.get("any", [])); any_ok = not any_fields or any(_present(row.get(field)) for field in any_fields); fresh = _fresh(row.get("boursorama_dynamic_age_hours"), cfg["boursorama"]["ci_dynamic_max_age_hours"]); reasons = []
    if missing_all: reasons.append("BOURSORAMA_MISSING:" + ",".join(missing_all))
    if not any_ok: reasons.append("BOURSORAMA_TARGET_MISSING")
    if not fresh: reasons.append("BOURSORAMA_DYNAMIC_STALE_OR_MISSING")
    return not reasons, reasons


def _etf_boursorama_ready(row: pd.Series, cfg: dict) -> tuple[bool, list[str]]:
    rule = cfg["boursorama"]["etf_ready_rule"]; fields = list(rule.get("fields", [])); present = sum(1 for field in fields if _present(row.get(field))); minimum = int(rule.get("minimum_present", 1)); fresh = _fresh(row.get("boursorama_etf_dynamic_age_hours"), cfg["boursorama"]["ci_dynamic_max_age_hours"]); reasons = []
    if present < minimum: reasons.append(f"BOURSORAMA_ETF_CONTEXT_{present}_OF_{minimum}")
    if not fresh: reasons.append("BOURSORAMA_ETF_DYNAMIC_STALE_OR_MISSING")
    return not reasons, reasons


def _context_coverage(row: pd.Series, fields: tuple[str, ...]) -> float:
    return round(sum(1 for field in fields if _present(row.get(field))) * 100.0 / len(fields), 2) if fields else 0.0


def apply_source_confirmation_gate(frame: pd.DataFrame, contract: dict) -> pd.DataFrame:
    """Label source readiness strictly after internal preselection; never mutate score/decision."""
    if frame.empty: return frame.copy()
    out = frame.copy(); required_state = str(contract["investing"].get("required_state", "STRONG_BUY")).upper(); horizon_mapping = {str(k).upper(): str(v).upper() for k, v in contract["investing"]["horizon_mapping"].items()}; max_investing_age = float(contract["investing"]["ci_max_age_hours"]); selected_statuses = {str(v).upper() for v in contract["scope"].get("preselection_statuses", [])}
    fully = str(contract["source_gate"]["fully_validated_state"]); timing_wait = str(contract["source_gate"]["waiting_timing_state"]); b_incomplete = str(contract["source_gate"]["boursorama_incomplete_state"]); sources_incomplete = str(contract["source_gate"]["sources_incomplete_state"])
    records: list[dict[str, Any]] = []
    for _, row in out.iterrows():
        asset = str(row.get("asset_class") or "").upper(); horizon = str(row.get("horizon") or "").upper(); decision = str(row.get("decision") or row.get("dynamic_decision") or "").upper(); expected = horizon_mapping.get(horizon); applicable = asset in {"ACTION", "ETF"} and horizon in APPLICABLE_HORIZONS and expected is not None and decision in selected_statuses
        if not applicable:
            records.append({"investing_required_timeframe": expected, "investing_required_state": required_state, "investing_timing_confirmed": False, "investing_source_fresh": False, "boursorama_priority_ready": False, "boursorama_context_coverage_pct": 0.0, "source_validation_state": "NOT_APPLICABLE", "source_validation_reasons": "NOT_PRESELECTED_OR_UNSUPPORTED", "source_fully_validated": False, "ci_source_eligible": False, "source_gate_can_create_buy": False, "source_gate_score_influence": 0.0}); continue
        if asset == "ACTION": b_ready, b_reasons = _action_boursorama_ready(row, contract); b_coverage = _context_coverage(row, ACTION_CONTEXT_FIELDS)
        else: b_ready, b_reasons = _etf_boursorama_ready(row, contract); b_coverage = _context_coverage(row, ETF_CONTEXT_FIELDS)
        signal = str(row.get("investing_horizon_signal") or "").strip().upper(); investing_fresh = _fresh(row.get("investing_age_hours"), max_investing_age); timing_ok = signal == required_state and investing_fresh; reasons = list(b_reasons)
        if not signal: reasons.append("INVESTING_SIGNAL_MISSING")
        elif signal != required_state: reasons.append(f"INVESTING_{expected}_{signal}_NOT_{required_state}")
        if not investing_fresh: reasons.append("INVESTING_STALE_OR_MISSING")
        state = fully if b_ready and timing_ok else timing_wait if b_ready else b_incomplete if timing_ok else sources_incomplete; source_full = state == fully
        records.append({"investing_required_timeframe": expected, "investing_required_state": required_state, "investing_timing_confirmed": timing_ok, "investing_source_fresh": investing_fresh, "boursorama_priority_ready": b_ready, "boursorama_context_coverage_pct": b_coverage, "source_validation_state": state, "source_validation_reasons": " | ".join(reasons) if reasons else "OK", "source_fully_validated": source_full, "ci_source_eligible": bool(decision == "BUY_CANDIDATE" and source_full), "source_gate_can_create_buy": False, "source_gate_score_influence": 0.0})
    gate = pd.DataFrame(records, index=out.index)
    for column in gate.columns: out[column] = gate[column]
    return out


def source_gate_summary(frame: pd.DataFrame) -> dict:
    if frame.empty or "source_validation_state" not in frame: return {"rows": int(len(frame)), "applicable_rows": 0, "fully_validated": 0, "ci_source_eligible": 0, "states": {}}
    applicable = frame["source_validation_state"].astype(str).ne("NOT_APPLICABLE"); states = frame.loc[applicable, "source_validation_state"].astype(str).value_counts(dropna=False).to_dict(); eligible = frame.get("ci_source_eligible", pd.Series(False, index=frame.index)).fillna(False).astype(bool); full = frame.get("source_fully_validated", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
    return {"rows": int(len(frame)), "applicable_rows": int(applicable.sum()), "fully_validated": int(full.sum()), "ci_source_eligible": int(eligible.sum()), "states": {str(k): int(v) for k, v in states.items()}, "decision_mutation": False, "score_influence": 0.0, "required_investing_state": "STRONG_BUY"}
