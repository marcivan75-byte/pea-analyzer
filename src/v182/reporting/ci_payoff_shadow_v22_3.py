from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/CI_PAYOFF_SHADOW_V22_3.json")
INPUT_CI = Path("outputs/committee_master/CI_RESULTS_CHALLENGER_V2.csv")
FALLBACK_CI = Path("outputs/committee_master/CI_SELECTION_ALL_V4.csv")
OUTPUT = Path("outputs/committee_master/CI_PAYOFF_SHADOW_V22_3.csv")
AUDIT = Path("outputs/audit/CI_PAYOFF_SHADOW_V22_3.json")


def _num(value) -> float | None:
    try:
        number = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _cfg(root: Path) -> dict:
    return json.loads((root / CONFIG).read_text(encoding="utf-8"))


def payoff_from_rr(rr: float | None, cfg: dict) -> tuple[float, str]:
    if rr is None:
        return 0.0, "RR_UNAVAILABLE"
    if rr < float(cfg.get("exclude_below_rr", 1.5)):
        return 0.0, "RR_BELOW_1_5"
    for item in cfg.get("payoff_breakpoints", []):
        if rr >= float(item["min_rr"]):
            return float(item["score"]), f"RR_{rr:.2f}"
    return 0.0, "RR_UNMAPPED"


def attach_payoff_shadow(frame: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    rr_field = str(cfg.get("rr_field", "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY"))
    cap = cfg.get("reliability_cap", {})
    min_rel = float(cap.get("min_reliability", 80.0))
    capped = float(cap.get("max_payoff_if_below", 70.0))
    selected_min = float(cfg.get("shadow_selected_min_rr", 2.0))
    weights = cfg.get("shadow_weights", {})
    payoffs: list[float] = []
    reasons: list[str] = []
    composites: list[float] = []
    labels: list[str] = []
    rrs: list[float | None] = []
    for _, row in result.iterrows():
        rr = _num(row.get(rr_field))
        payoff, reason = payoff_from_rr(rr, cfg)
        reliability = _num(row.get("SIM_RELIABILITY"))
        if reliability is not None and reliability < min_rel and payoff > capped:
            payoff = capped
            reason = f"{reason}|RELIABILITY_CAP_{reliability:.1f}"
        component = {
            "reward_risk": payoff,
            "entry_timing": _num(row.get("v22_2_component_entry_timing")) or 0.0,
            "trend_momentum": _num(row.get("v22_2_component_trend_momentum")) or 0.0,
            "selection_coverage": _num(row.get("v22_2_component_selection_coverage")) or _num(row.get("coverage_pct")) or 0.0,
            "temporal_stability": _num(row.get("v22_2_component_temporal_stability")) or 0.0,
            "provenance_quality": _num(row.get("v22_2_component_provenance_quality")) or 0.0,
        }
        composite = sum(float(weights.get(name, 0.0)) * value for name, value in component.items())
        if rr is not None and rr >= selected_min and payoff > 0:
            label = "SHADOW_SELECTED_RR"
        elif rr is None:
            label = "SHADOW_WAIT_RR_MISSING"
        else:
            label = "SHADOW_EXCLUDE_RR"
        payoffs.append(round(payoff, 2))
        reasons.append(reason)
        composites.append(round(composite, 2))
        labels.append(label)
        rrs.append(None if rr is None else round(rr, 4))
    result["CI_RR_AT_ENTRY"] = rrs
    result["CI_PAYOFF_SCORE_V22_3"] = payoffs
    result["CI_PAYOFF_REASON_V22_3"] = reasons
    result["CI_SHADOW_COMPOSITE_V22_3"] = composites
    result["CI_SHADOW_DECISION_V22_3"] = labels
    result["CI_OFFICIAL_CONFIDENCE_UNCHANGED"] = True
    result["CI_V4_GATE_UNCHANGED"] = True
    return result


def run(root: Path = ROOT) -> dict:
    cfg = _cfg(root)
    source = root / INPUT_CI if (root / INPUT_CI).exists() else root / FALLBACK_CI
    frame = attach_payoff_shadow(_read(source), cfg)
    generated = datetime.now(timezone.utc).isoformat()
    out = root / OUTPUT
    audit = root / AUDIT
    out.parent.mkdir(parents=True, exist_ok=True)
    audit.parent.mkdir(parents=True, exist_ok=True)
    preferred = [
        "name", "isin", "asset_class", "horizon", "score", "CI_CONFIDENCE_SCORE_V22_2_1",
        "CI_SELECTION_GATE_STATUS_V4", "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", "SIM_RELIABILITY",
        "CI_RR_AT_ENTRY", "CI_PAYOFF_SCORE_V22_3", "CI_PAYOFF_REASON_V22_3",
        "CI_SHADOW_COMPOSITE_V22_3", "CI_SHADOW_DECISION_V22_3",
    ]
    cols = [c for c in preferred if c in frame.columns] + [c for c in frame.columns if c not in preferred]
    if not frame.empty:
        frame[cols].to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=preferred).to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    selected_rr = int(frame["CI_SHADOW_DECISION_V22_3"].eq("SHADOW_SELECTED_RR").sum()) if not frame.empty else 0
    payload = {
        "status": "SUCCESS",
        "version": cfg.get("version"),
        "generated_at_utc": generated,
        "rows": int(len(frame)),
        "shadow_selected_rr": selected_rr,
        "official_confidence_changed": False,
        "ci_v4_gate_changed": False,
        "real_orders_enabled": False,
        "outputs": {"csv": OUTPUT.as_posix()},
    }
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2))
