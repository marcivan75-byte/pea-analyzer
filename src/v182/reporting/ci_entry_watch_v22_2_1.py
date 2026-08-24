from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import pandas as pd

from v182.reporting import ci_entry_watch_v22_2 as previous
from v182.reporting import market_orientation_v22_2

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/CI_ENTRY_GOVERNANCE_V22_2_1.json")
OUTPUT = Path("outputs/committee_master/CI_ENTRY_WATCH_V22_2_1.csv")
MOBILE_MD = Path("outputs/mobile/ANDROID_CI_ENTRY_WATCH_V22_2_1.md")
AUDIT = Path("outputs/audit/CI_ENTRY_WATCH_V22_2_1.json")


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


def _num(value) -> float | None:
    try:
        x = float(pd.to_numeric(value, errors="coerce"))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _cfg(root: Path) -> dict:
    path = root / CONFIG
    return json.loads(path.read_text(encoding="utf-8"))


def _metadata(root: Path) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for asset, paths in (
        ("ACTION", [root / "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ACTIONS_MASTER.csv"]),
        ("ETF", [root / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv", root / "inputs/V18.2_PEA_ETF_MASTER.csv"]),
    ):
        path = next((p for p in paths if p.exists()), None)
        frame = _read(path) if path else pd.DataFrame()
        if frame.empty or "isin" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            isin = _text(row.get("isin"))
            if isin:
                out[(asset, isin)] = row.to_dict()
    return out


def _potential(asset: str, meta: dict, cfg: dict) -> tuple[float | None, str, float | None]:
    policy = cfg["potential"]
    lo = float(policy.get("plausible_min_pct", -100.0))
    hi = float(policy.get("plausible_max_pct", 300.0))
    close = _num(meta.get("last_close"))

    def valid(value):
        n = _num(value)
        return n if n is not None and lo <= n <= hi else None

    if asset == "ACTION":
        for field, method in (("upside_pct", "CONSENSUS_UPSIDE"), ("upside_pct_yf", "YAHOO_CONSENSUS_UPSIDE")):
            value = valid(meta.get(field))
            if value is not None:
                target = None if close is None else close * (1.0 + value / 100.0)
                return round(value, 2), method, round(target, 4) if target is not None else None
        for field, method in (("target_price", "CONSENSUS_TARGET_PRICE"), ("target_mean_yf", "YAHOO_TARGET_MEAN")):
            target = _num(meta.get(field))
            if close is not None and close > 0 and target is not None and target > 0:
                value = valid((target / close - 1.0) * 100.0)
                if value is not None:
                    return round(value, 2), method, round(target, 4)

    high = _num(meta.get("high_52w"))
    if close is not None and close > 0 and high is not None and high > 0:
        value = valid((high / close - 1.0) * 100.0)
        if value is not None:
            return round(value, 2), "TECHNICAL_TO_52W_HIGH", round(high, 4)
    return None, "UNAVAILABLE", None


def _market_scope(row: pd.Series, meta: dict, cfg: dict) -> str:
    asset = _text(row.get("asset_class")).upper()
    if asset == "ACTION":
        return str(cfg["market_entry_gate"].get("action_market_scope", "EUROPE")).upper()
    zone = " ".join(_text(meta.get(k)).upper() for k in ("geographic_zone", "zone_geo", "region", "name"))
    if any(token in zone for token in ("EUROPE", "EURO", "EMU", "STOXX", "EUROZONE")):
        return "EUROPE"
    return str(cfg["market_entry_gate"].get("etf_default_market_scope", "GLOBAL")).upper()


def _orientation_for_scope(row: pd.Series, scope: str) -> str:
    field = {
        "EUROPE": "CI_MARKET_ORIENTATION_EUROPE",
        "US": "CI_MARKET_ORIENTATION_US",
        "GLOBAL": "CI_MARKET_ORIENTATION_GLOBAL",
    }.get(scope, "CI_MARKET_ORIENTATION_GLOBAL")
    return _text(row.get(field)).upper() or "UNKNOWN"


def _market_gate(row: pd.Series, meta: dict, cfg: dict) -> tuple[str, str, float, str]:
    policy = cfg["market_entry_gate"]
    adjustment = cfg["confidence_adjustment"]
    scope = _market_scope(row, meta, cfg)
    orientation = _orientation_for_scope(row, scope)
    cnn = _num(row.get("CI_MARKET_CNN_FEAR_GREED"))
    extreme_greed = cnn is not None and cnn > float(policy.get("cnn_extreme_greed_threshold", 75.0))

    if orientation == "RISK_OFF" and bool(policy.get("risk_off_blocks_entry_review", True)):
        return "BLOCK", f"{scope}_RISK_OFF", float(adjustment.get("risk_off_points", -15.0)), scope
    if extreme_greed:
        return "CAUTION", "CNN_EXTREME_GREED_OVERHEAT", float(adjustment.get("extreme_greed_points", -5.0)), scope
    if orientation == "RISK_ON":
        return "PASS", f"{scope}_RISK_ON", float(adjustment.get("risk_on_points", 5.0)), scope
    if orientation == "NEUTRAL":
        return "PASS", f"{scope}_NEUTRAL", float(adjustment.get("neutral_points", 0.0)), scope
    return "CAUTION", f"{scope}_MARKET_CONTEXT_UNKNOWN", 0.0, scope


def _final_entry_state(row: pd.Series, gate: str, confidence: float, cfg: dict) -> tuple[str, str]:
    base = _text(row.get("v22_2_entry_state")).upper()
    if base != "READY_FOR_REVIEW":
        return "WAIT", "BASE_TECHNICAL_TRIGGER_NOT_READY"
    if gate == "BLOCK":
        return "WAIT", "MARKET_ORIENTATION_BLOCK"
    if gate == "CAUTION":
        minimum = float(cfg["market_entry_gate"].get("minimum_confidence_for_caution_review", 70.0))
        if confidence < minimum:
            return "WAIT", "MARKET_CAUTION_CONFIDENCE_TOO_LOW"
    return "READY_FOR_REVIEW", "TECHNICAL_AND_MARKET_ENTRY_GATES_PASSED"


def _markdown(frame: pd.DataFrame, generated: str) -> str:
    lines = [
        "# CI Entry Watch V22.2.1",
        "",
        f"Generated: {generated}",
        "",
        "Selection score and selection decision remain unchanged. V22.2.1 only governs entry review; no real order is generated.",
        "",
    ]
    if frame.empty:
        return "\n".join(lines + ["No monitored CI candidates."]) + "\n"
    ready = int(frame["V22_2_1_ENTRY_STATE"].astype(str).eq("READY_FOR_REVIEW").sum())
    lines += [f"Candidates: {len(frame)} | READY_FOR_REVIEW: {ready} | WAIT: {len(frame)-ready}", ""]
    ordered = frame.sort_values(["V22_2_1_ENTRY_STATE", "CI_CONFIDENCE_SCORE_V22_2_1"], ascending=[True, False])
    for _, row in ordered.iterrows():
        pot = row.get("CI_POTENTIAL_UPSIDE_PCT")
        pot_txt = "NA" if pd.isna(pot) else f"{float(pot):.1f}%"
        lines.append(
            f"- {_text(row.get('name')) or _text(row.get('isin'))} | {_text(row.get('asset_class'))} {_text(row.get('horizon'))} | "
            f"score={row.get('score')} | confidence={row.get('CI_CONFIDENCE_SCORE_V22_2_1')} | potential={pot_txt} | "
            f"market={_text(row.get('CI_MARKET_GATE'))}/{_text(row.get('CI_MARKET_SCOPE'))} | "
            f"entry={_text(row.get('V22_2_1_ENTRY_STATE'))} | {_text(row.get('V22_2_1_ENTRY_REASON'))}"
        )
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    cfg = _cfg(root)
    # If this wrapper is run standalone, ensure the lightweight market context exists first.
    market_file = root / previous.MARKET_ORIENTATION
    if not market_file.exists():
        market_orientation_v22_2.run(root=root)

    base_payload = previous.run(root=root)
    if base_payload.get("status") != "SUCCESS":
        return {"status": "BLOCKED_V22_2", "base": base_payload}
    frame = _read(root / previous.OUTPUT)
    meta_map = _metadata(root)
    potentials: list[float | None] = []
    potential_methods: list[str] = []
    potential_refs: list[float | None] = []
    gates: list[str] = []
    gate_reasons: list[str] = []
    market_scopes: list[str] = []
    confidences: list[float] = []
    final_states: list[str] = []
    final_reasons: list[str] = []

    for _, row in frame.iterrows():
        asset = _text(row.get("asset_class")).upper()
        isin = _text(row.get("isin"))
        meta = meta_map.get((asset, isin), {})
        potential, method, ref = _potential(asset, meta, cfg)
        gate, reason, points, scope = _market_gate(row, meta, cfg)
        base_conf = _num(row.get("CI_CONFIDENCE_SCORE_0_100")) or 0.0
        low = float(cfg["confidence_adjustment"].get("min", 0.0)); high = float(cfg["confidence_adjustment"].get("max", 100.0))
        confidence = round(max(low, min(high, base_conf + points)), 2)
        state, final_reason = _final_entry_state(row, gate, confidence, cfg)
        potentials.append(potential); potential_methods.append(method); potential_refs.append(ref)
        gates.append(gate); gate_reasons.append(reason); market_scopes.append(scope); confidences.append(confidence)
        final_states.append(state); final_reasons.append(final_reason)

    frame["CI_POTENTIAL_UPSIDE_PCT"] = potentials
    frame["CI_POTENTIAL_METHOD"] = potential_methods
    frame["CI_POTENTIAL_REFERENCE_LEVEL"] = potential_refs
    frame["CI_MARKET_GATE"] = gates
    frame["CI_MARKET_GATE_REASON"] = gate_reasons
    frame["CI_MARKET_SCOPE"] = market_scopes
    frame["CI_CONFIDENCE_SCORE_V22_2_1"] = confidences
    frame["V22_2_1_ENTRY_STATE"] = final_states
    frame["V22_2_1_ENTRY_REASON"] = final_reasons
    frame["CI_AUTOMATIC_ORDER_ALLOWED"] = False
    frame["CI_SELECTION_SCORE_CHANGED_V22_2_1"] = False
    generated = datetime.now(timezone.utc).isoformat()
    frame["CI_V22_2_1_GENERATED_AT_UTC"] = generated

    out = root / OUTPUT; md = root / MOBILE_MD; audit = root / AUDIT
    for path in (out, md, audit):
        path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    md.write_text(_markdown(frame, generated), encoding="utf-8")
    payload = {
        "status": "SUCCESS",
        "version": "V22.2.1_CI_ENTRY_WATCH",
        "generated_at_utc": generated,
        "candidate_rows": int(len(frame)),
        "ready_for_review": int(frame["V22_2_1_ENTRY_STATE"].eq("READY_FOR_REVIEW").sum()) if not frame.empty else 0,
        "wait": int(frame["V22_2_1_ENTRY_STATE"].eq("WAIT").sum()) if not frame.empty else 0,
        "market_blocks": int(frame["CI_MARKET_GATE"].eq("BLOCK").sum()) if not frame.empty else 0,
        "market_cautions": int(frame["CI_MARKET_GATE"].eq("CAUTION").sum()) if not frame.empty else 0,
        "potential_available": int(frame["CI_POTENTIAL_UPSIDE_PCT"].notna().sum()) if not frame.empty else 0,
        "selection_score_changed": False,
        "selection_decision_changed": False,
        "wave09_reintroduced": False,
        "real_orders_enabled": False,
        "outputs": {"csv": str(OUTPUT), "mobile_markdown": str(MOBILE_MD)},
        "base": base_payload,
    }
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2))
