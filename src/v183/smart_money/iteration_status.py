from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / "state/smart_money"
OUT = ROOT / "outputs/audit/V18.3_SMART_MONEY_ITERATION_STATUS.json"
CONFIG = ROOT / "config/V18.3_SMART_MONEY_CONFIG.json"


def _dates_from(path: Path, field: str) -> set[str]:
    if not path.exists():
        return set()
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return set()
    if field not in frame.columns:
        return set()
    values = pd.to_datetime(frame[field], errors="coerce", utc=True).dropna()
    return {x.date().isoformat() for x in values}


def build() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cal = cfg.get("calibration", {})
    required = int(cal.get("minimum_distinct_run_dates_for_empirical_fit", 20))
    required_obs = int(cal.get("minimum_shadow_observations_for_empirical_fit", 20))

    score_path = STATE / "SMART_MONEY_DAILY_SCORES.parquet"
    flow_path = STATE / "ETF_FLOW_HISTORY.parquet"
    event_path = STATE / "SMART_MONEY_EVENTS.parquet"

    score_dates = _dates_from(score_path, "as_of")
    flow_dates = _dates_from(flow_path, "date")
    event_dates = _dates_from(event_path, "publication_date")

    score_rows = 0
    etf_score_rows = 0
    action_score_rows = 0
    etf_unique = 0
    action_unique = 0
    if score_path.exists():
        try:
            scores = pd.read_parquet(score_path)
            score_rows = len(scores)
            universe = scores.get("universe", pd.Series("", index=scores.index)).astype(str)
            etf = scores[universe.eq("ETF")]
            actions = scores[universe.eq("ACTION")]
            etf_score_rows = len(etf)
            action_score_rows = len(actions)
            if "isin" in scores.columns:
                etf_unique = int(etf["isin"].astype(str).nunique()) if not etf.empty else 0
                action_unique = int(actions["isin"].astype(str).nunique()) if not actions.empty else 0
        except Exception:
            pass

    flow_rows = 0
    flow_isins = 0
    if flow_path.exists():
        try:
            flows = pd.read_parquet(flow_path)
            flow_rows = len(flows)
            flow_isins = int(flows["isin"].astype(str).nunique()) if "isin" in flows.columns else 0
        except Exception:
            pass

    distinct_dates = len(score_dates)
    empirical_ready = distinct_dates >= required and score_rows >= required_obs
    positive_activation_ready = bool(empirical_ready and cal.get("active_scoring_allowed") is True)

    return {
        "passed": True,
        "mode": "PERSISTENT_ITERATIVE_SHADOW",
        "score_application": cfg.get("score_application"),
        "shadow_mode": cfg.get("shadow_mode"),
        "distinct_shadow_run_dates": distinct_dates,
        "shadow_run_dates": sorted(score_dates),
        "minimum_distinct_shadow_run_dates": required,
        "minimum_shadow_observations": required_obs,
        "score_state_rows": score_rows,
        "etf_score_state_rows": etf_score_rows,
        "action_score_state_rows": action_score_rows,
        "etf_unique_isin_in_state": etf_unique,
        "action_unique_isin_in_state": action_unique,
        "etf_flow_history_rows": flow_rows,
        "etf_flow_history_isins": flow_isins,
        "etf_flow_dates": sorted(flow_dates),
        "event_dates": sorted(event_dates),
        "empirical_walk_forward_minimum_history_reached": empirical_ready,
        "positive_score_activation_ready": positive_activation_ready,
        "negative_high_confidence_risk_gate_can_be_used": True,
        "reason": (
            "Positive scoring remains disabled until empirical walk-forward history is sufficient and calibration explicitly authorizes it. "
            "Negative high-confidence Smart Money signals may already operate as a conservative decision gate."
        ),
    }


def main() -> None:
    result = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("SMART_MONEY_ITERATION_STATUS_OK", json.dumps({
        "dates": result["distinct_shadow_run_dates"],
        "etf_unique": result["etf_unique_isin_in_state"],
        "positive_ready": result["positive_score_activation_ready"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
