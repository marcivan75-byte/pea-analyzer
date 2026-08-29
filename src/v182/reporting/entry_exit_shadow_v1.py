"""Shadow entry/exit policies. Does not change CI_SELECTION_GATE."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = Path("config/ENTRY_EXIT_SHADOW_POLICY_V1.json")
CI_PATHS = (
    Path("outputs/committee_master/CI_RESULTS_CHALLENGER_V2.csv"),
    Path("outputs/committee_master/CI_SELECTION_ALL_V4.csv"),
)
STATE_PATH = Path("state/objectives_risk/ENTRY_EXIT_SHADOW_STATE.json")
OUT_CSV = Path("outputs/committee_master/ENTRY_EXIT_SHADOW_V1.csv")
OUT_MD = Path("outputs/mobile/ENTRY_EXIT_SHADOW_V1.md")
OUT_AUDIT = Path("outputs/audit/ENTRY_EXIT_SHADOW_V1.json")


def _load_policy(root: Path) -> dict:
    path = root / POLICY_PATH
    if path.exists() and path.stat().st_size:
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _read_ci(root: Path) -> pd.DataFrame:
    for relative in CI_PATHS:
        path = root / relative
        if path.exists() and path.stat().st_size:
            return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    return pd.DataFrame()


def _num(value) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        text = str(value).replace(",", ".").strip()
        if text in {"", "nan", "None"}:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _load_state(root: Path) -> dict:
    path = root / STATE_PATH
    if path.exists() and path.stat().st_size:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            return {"names": {}}
    return {"names": {}}


def _annotate(frame: pd.DataFrame, policy: dict, state: dict) -> tuple[pd.DataFrame, dict]:
    baseline = policy.get("baseline_A") or {}
    chal_b = policy.get("challenger_B") or {}
    chal_c = policy.get("challenger_C") or {}
    hyst = policy.get("exit_hysteresis") or {}
    conf_a = float(baseline.get("action_confidence_min", 66))
    upside_a = float(baseline.get("action_upside_min_pct", 20))
    conf_b = float(chal_b.get("action_confidence_min", 55))
    rr_b = float(chal_b.get("require_rr_min", 2))
    or_c = float(chal_c.get("or_composite_min", 42))
    rr_c = float(chal_c.get("require_rr_min", 2))
    buffer_pts = float(hyst.get("confidence_buffer_pts", 8))
    weeks_below = int(hyst.get("weeks_below", 2))
    names_state = dict(state.get("names") or {})
    rows = []
    for _, row in frame.iterrows():
        name = str(row.get("name") or row.get("isin") or "")
        asset = str(row.get("asset_class") or "").upper()
        conf = _num(row.get("CI_CONFIDENCE_SCORE_V22_2_1"))
        rr = _num(row.get("SIM_REWARD_RISK_AT_OPTIMAL_ENTRY"))
        or_score = _num(row.get("OR_COMPOSITE_SHADOW"))
        gate = str(row.get("CI_SELECTION_GATE_STATUS_V4") or "")
        entry_state = str(row.get("CHALLENGER_ENTRY_STATE") or "")
        price = _num(row.get("SIM_CURRENT_PRICE"))
        invalid = _num(row.get("SIM_INVALIDATION"))
        baseline_entry = gate == "SELECTED"
        soft_b = bool(
            conf is not None and conf >= conf_b and rr is not None and rr >= rr_b
        )
        soft_c = bool(
            or_score is not None and or_score >= or_c and rr is not None and rr >= rr_c
        )
        if asset == "ACTION":
            baseline_entry = bool(
                conf is not None and conf >= conf_a and gate == "SELECTED"
            )
        prev = names_state.get(name) or {}
        below = int(prev.get("weeks_below_entry", 0))
        watch = soft_b or soft_c
        if watch and conf is not None and conf < conf_b:
            below += 1
        elif watch:
            below = 0
        exit_invalid = bool(price is not None and invalid is not None and price < invalid)
        exit_hyst = below >= weeks_below and (soft_b or soft_c or prev.get("was_watch"))
        if exit_invalid:
            shadow_action = "EXIT_INVALIDATION_SHADOW"
        elif exit_hyst:
            shadow_action = "EXIT_HYSTERESIS_SHADOW"
        elif baseline_entry and entry_state == "READY":
            shadow_action = "ENTER_BASELINE_SHADOW"
        elif soft_c:
            shadow_action = "WATCH_OR_RR_SHADOW"
        elif soft_b:
            shadow_action = "WATCH_CONFIDENCE_SHADOW"
        else:
            shadow_action = "HOLD_OR_WAIT_SHADOW"
        names_state[name] = {
            "weeks_below_entry": below,
            "was_watch": watch,
            "last_confidence": conf,
            "last_action": shadow_action,
        }
        rec = dict(row)
        rec["ENTRY_BASELINE_A"] = baseline_entry
        rec["ENTRY_CHALLENGER_B_WATCH"] = soft_b
        rec["ENTRY_CHALLENGER_C_WATCH"] = soft_c
        rec["EXIT_INVALIDATION_SHADOW"] = exit_invalid
        rec["EXIT_HYSTERESIS_SHADOW"] = exit_hyst
        rec["ENTRY_EXIT_SHADOW_ACTION"] = shadow_action
        rec["ENTRY_EXIT_WEEKS_BELOW"] = below
        rec["ENTRY_EXIT_HYSTERESIS_BUFFER"] = buffer_pts
        rec["ENTRY_EXIT_DECISION_INFLUENCE"] = 0.0
        rows.append(rec)
    new_state = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "names": names_state,
        "decision_influence": 0.0,
    }
    return pd.DataFrame(rows), new_state


def _markdown(frame: pd.DataFrame, policy: dict) -> str:
    lines = [
        "# Entrée / sortie SHADOW V1",
        "",
        "Baseline A = gate production (confiance 66). B et C sont des watchers.",
        "Aucune promotion, aucun ordre, hysteresis 8 pts / 2 vendredis.",
        "",
        f"Verrouillé: {policy.get('locked_at_utc')} — OOS {policy.get('oos_start')}",
        "",
        "| name | asset | conf | R:R | O/R | baseline_A | watch_B | watch_C | action_shadow |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    use = frame.head(20) if not frame.empty else frame
    for _, row in use.iterrows():
        lines.append(
            "| {name} | {asset} | {conf} | {rr} | {ors} | {a} | {b} | {c} | {act} |".format(
                name=row.get("name", ""),
                asset=row.get("asset_class", ""),
                conf=row.get("CI_CONFIDENCE_SCORE_V22_2_1", ""),
                rr=row.get("SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", ""),
                ors=row.get("OR_COMPOSITE_SHADOW", ""),
                a=row.get("ENTRY_BASELINE_A", ""),
                b=row.get("ENTRY_CHALLENGER_B_WATCH", ""),
                c=row.get("ENTRY_CHALLENGER_C_WATCH", ""),
                act=row.get("ENTRY_EXIT_SHADOW_ACTION", ""),
            )
        )
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    policy = _load_policy(root)
    frame = _read_ci(root)
    state = _load_state(root)
    if frame.empty:
        payload = {"status": "SKIPPED_NO_CI", "rows": 0, "decision_influence": 0.0}
    else:
        annotated, state = _annotate(frame, policy, state)
        (root / OUT_CSV).parent.mkdir(parents=True, exist_ok=True)
        annotated.to_csv(root / OUT_CSV, sep=";", index=False, encoding="utf-8-sig")
        (root / OUT_MD).parent.mkdir(parents=True, exist_ok=True)
        (root / OUT_MD).write_text(_markdown(annotated, policy), encoding="utf-8")
        payload = {
            "status": "SUCCESS",
            "rows": int(len(annotated)),
            "baseline_true": int(annotated["ENTRY_BASELINE_A"].sum()) if "ENTRY_BASELINE_A" in annotated else 0,
            "watch_b": int(annotated["ENTRY_CHALLENGER_B_WATCH"].sum()) if "ENTRY_CHALLENGER_B_WATCH" in annotated else 0,
            "watch_c": int(annotated["ENTRY_CHALLENGER_C_WATCH"].sum()) if "ENTRY_CHALLENGER_C_WATCH" in annotated else 0,
            "decision_influence": 0.0,
            "real_orders_enabled": False,
            "production_gates_unchanged": True,
        }
    (root / STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / STATE_PATH).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / OUT_AUDIT).parent.mkdir(parents=True, exist_ok=True)
    payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload["policy_version"] = policy.get("version")
    (root / OUT_AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
