"""Shadow entry/exit with a weekly dynamic confidence threshold."""
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
            return {"names": {}, "thresholds": {}}
    return {"names": {}, "thresholds": {}}


def _dynamic_threshold(values: list[float], spec: dict, previous: float | None) -> float:
    floor = float(spec.get("floor", 58))
    cap = float(spec.get("cap", 72))
    quantile = float(spec.get("quantile", 0.70))
    smoothing = float(spec.get("smoothing", 0.5))
    min_sample = int(spec.get("min_sample", 4))
    raw = floor
    if len(values) >= min_sample:
        series = pd.Series(values)
        raw = float(series.quantile(quantile))
    elif values:
        raw = float(sorted(values)[len(values) // 2])
    raw = max(floor, min(cap, raw))
    if previous is None:
        return round(raw, 2)
    blended = smoothing * raw + (1.0 - smoothing) * float(previous)
    return round(max(floor, min(cap, blended)), 2)


def _annotate(frame: pd.DataFrame, policy: dict, state: dict) -> tuple[pd.DataFrame, dict, dict]:
    baseline = policy.get("baseline_A") or {}
    dyn = policy.get("dynamic_confidence") or {}
    chal_c = policy.get("challenger_C") or {}
    hyst = policy.get("exit_hysteresis") or {}
    conf_a = float(baseline.get("action_confidence_min", 66))
    rr_min = float(dyn.get("require_rr_min", 2))
    or_c = float(chal_c.get("or_composite_min", 42))
    rr_c = float(chal_c.get("require_rr_min", 2))
    buffer_pts = float(hyst.get("confidence_buffer_pts", 8))
    weeks_needed = int(hyst.get("weeks_below", 2))
    prev_thresholds = dict(state.get("thresholds") or {})
    by_asset: dict[str, list[float]] = {"ACTION": [], "ETF": []}
    for _, row in frame.iterrows():
        asset = str(row.get("asset_class") or "").upper()
        conf = _num(row.get("CI_CONFIDENCE_SCORE_V22_2_1"))
        if conf is None:
            continue
        key = "ETF" if asset == "ETF" else "ACTION"
        by_asset[key].append(conf)
    thresholds = {}
    for key, values in by_asset.items():
        previous = prev_thresholds.get(key)
        thresholds[key] = _dynamic_threshold(values, dyn, previous if previous is not None else None)
    names_state = dict(state.get("names") or {})
    rows = []
    for _, row in frame.iterrows():
        name = str(row.get("name") or row.get("isin") or "")
        asset = str(row.get("asset_class") or "").upper()
        bucket = "ETF" if asset == "ETF" else "ACTION"
        dyn_th = thresholds.get(bucket, float(dyn.get("floor", 58)))
        conf = _num(row.get("CI_CONFIDENCE_SCORE_V22_2_1"))
        rr = _num(row.get("SIM_REWARD_RISK_AT_OPTIMAL_ENTRY"))
        or_score = _num(row.get("OR_COMPOSITE_SHADOW"))
        gate = str(row.get("CI_SELECTION_GATE_STATUS_V4") or "")
        entry_state = str(row.get("CHALLENGER_ENTRY_STATE") or "")
        price = _num(row.get("SIM_CURRENT_PRICE"))
        invalid = _num(row.get("SIM_INVALIDATION"))
        baseline_entry = gate == "SELECTED"
        if asset == "ACTION":
            baseline_entry = bool(conf is not None and conf >= conf_a and gate == "SELECTED")
        dynamic_hit = bool(
            conf is not None and conf >= dyn_th and rr is not None and rr >= rr_min
        )
        soft_c = bool(or_score is not None and or_score >= or_c and rr is not None and rr >= rr_c)
        prev = names_state.get(name) or {}
        below = int(prev.get("weeks_below_entry", 0))
        if prev.get("was_dynamic") and conf is not None and conf < (dyn_th - buffer_pts):
            below += 1
        elif dynamic_hit:
            below = 0
        exit_invalid = bool(price is not None and invalid is not None and price < invalid)
        exit_hyst = below >= weeks_needed and bool(prev.get("was_dynamic"))
        if exit_invalid:
            shadow_action = "EXIT_INVALIDATION_SHADOW"
        elif exit_hyst:
            shadow_action = "EXIT_HYSTERESIS_SHADOW"
        elif baseline_entry and entry_state == "READY":
            shadow_action = "ENTER_BASELINE_SHADOW"
        elif dynamic_hit:
            shadow_action = "ENTER_DYNAMIC_SELECTIVE_SHADOW"
        elif soft_c:
            shadow_action = "WATCH_OR_RR_SHADOW"
        else:
            shadow_action = "HOLD_OR_WAIT_SHADOW"
        names_state[name] = {
            "weeks_below_entry": below,
            "was_dynamic": dynamic_hit or bool(prev.get("was_dynamic")) and not exit_hyst and not exit_invalid,
            "last_confidence": conf,
            "last_dynamic_threshold": dyn_th,
            "last_action": shadow_action,
        }
        rec = dict(row)
        rec["ENTRY_BASELINE_A"] = baseline_entry
        rec["ENTRY_DYNAMIC_THRESHOLD"] = dyn_th
        rec["ENTRY_DYNAMIC_SELECTIVE"] = dynamic_hit
        rec["ENTRY_CHALLENGER_C_WATCH"] = soft_c
        rec["EXIT_INVALIDATION_SHADOW"] = exit_invalid
        rec["EXIT_HYSTERESIS_SHADOW"] = exit_hyst
        rec["ENTRY_EXIT_SHADOW_ACTION"] = shadow_action
        rec["ENTRY_EXIT_WEEKS_BELOW"] = below
        rec["ENTRY_EXIT_DECISION_INFLUENCE"] = 0.0
        rows.append(rec)
    new_state = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "names": names_state,
        "thresholds": thresholds,
        "threshold_history": list(state.get("threshold_history") or []) + [
            {"as_of_utc": datetime.now(timezone.utc).isoformat(), **thresholds}
        ],
        "decision_influence": 0.0,
    }
    new_state["threshold_history"] = new_state["threshold_history"][-24:]
    return pd.DataFrame(rows), new_state, thresholds


def _markdown(frame: pd.DataFrame, policy: dict, thresholds: dict) -> str:
    dyn = policy.get("dynamic_confidence") or {}
    lines = [
        "# Entrée / sortie SHADOW — confiance dynamique",
        "",
        f"Seuil du vendredi: ACTION={thresholds.get('ACTION')} ETF={thresholds.get('ETF')}",
        f"Règle: max({dyn.get('floor')}, min({dyn.get('cap')}, P{int(float(dyn.get('quantile', 0.7))*100)} lissé)).",
        "Le 66 de production reste le gate officiel. Le dynamique est shadow, sélectif, borné.",
        f"Verrouillé: {policy.get('locked_at_utc')} — OOS {policy.get('oos_start')}",
        "",
        "| name | asset | conf | seuil_dyn | R:R | baseline_66 | dyn_ok | action_shadow |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, row in frame.head(20).iterrows() if not frame.empty else []:
        lines.append(
            "| {name} | {asset} | {conf} | {th} | {rr} | {a} | {d} | {act} |".format(
                name=row.get("name", ""),
                asset=row.get("asset_class", ""),
                conf=row.get("CI_CONFIDENCE_SCORE_V22_2_1", ""),
                th=row.get("ENTRY_DYNAMIC_THRESHOLD", ""),
                rr=row.get("SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", ""),
                a=row.get("ENTRY_BASELINE_A", ""),
                d=row.get("ENTRY_DYNAMIC_SELECTIVE", ""),
                act=row.get("ENTRY_EXIT_SHADOW_ACTION", ""),
            )
        )
    return "\n".join(lines) + "\n"


def run(root: Path = ROOT) -> dict:
    policy = _load_policy(root)
    frame = _read_ci(root)
    state = _load_state(root)
    thresholds: dict = {}
    if frame.empty:
        payload = {"status": "SKIPPED_NO_CI", "rows": 0, "decision_influence": 0.0}
    else:
        annotated, state, thresholds = _annotate(frame, policy, state)
        (root / OUT_CSV).parent.mkdir(parents=True, exist_ok=True)
        annotated.to_csv(root / OUT_CSV, sep=";", index=False, encoding="utf-8-sig")
        (root / OUT_MD).parent.mkdir(parents=True, exist_ok=True)
        (root / OUT_MD).write_text(_markdown(annotated, policy, thresholds), encoding="utf-8")
        payload = {
            "status": "SUCCESS",
            "rows": int(len(annotated)),
            "dynamic_thresholds": thresholds,
            "dynamic_selected": int(annotated["ENTRY_DYNAMIC_SELECTIVE"].sum()) if "ENTRY_DYNAMIC_SELECTIVE" in annotated else 0,
            "baseline_true": int(annotated["ENTRY_BASELINE_A"].sum()) if "ENTRY_BASELINE_A" in annotated else 0,
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
