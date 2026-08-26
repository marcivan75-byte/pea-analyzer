from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/OBJECTIVES_RISK_CHALLENGER_V2.json")
INPUT = Path("outputs/committee_master/OBJECTIVES_RISK_SHADOW_V1.csv")
OUTPUT = Path("outputs/committee_master/OBJECTIVES_RISK_CHALLENGER_V2.csv")
AUDIT = Path("outputs/audit/OBJECTIVES_RISK_CHALLENGER_V2.json")


def _num(frame: pd.DataFrame, *names: str) -> pd.Series:
    result = pd.Series(np.nan, index=frame.index, dtype=float)
    for name in names:
        if name in frame:
            result = result.combine_first(pd.to_numeric(frame[name], errors="coerce"))
    return result


def _orientation(frame: pd.DataFrame) -> pd.Series:
    for name in ("CI_MARKET_ORIENTATION_EUROPE", "orientation_europe", "orientation_global"):
        if name in frame:
            return frame[name].fillna("NEUTRAL").astype(str).str.upper()
    return pd.Series("NEUTRAL", index=frame.index)


def _lower_is_better_score(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(pct=True, ascending=False, na_option="keep").mul(100.0)


def _source_confidence(frame: pd.DataFrame) -> pd.Series:
    groups = [
        ["boursorama_consensus", "boursorama_etf_pea_eligible_displayed"],
        ["tradingview_daily_signal"], ["tradingview_weekly_signal"], ["tradingview_monthly_signal"],
        ["SIM_CURRENT_PRICE", "SIM_ENTRY_OPTIMAL", "SIM_INVALIDATION"],
    ]
    evidence = []
    for alternatives in groups:
        present = pd.Series(False, index=frame.index)
        for field in alternatives:
            if field in frame:
                values = frame[field]
                present |= values.notna() & ~values.astype(str).str.strip().isin({"", "nan", "None"})
        evidence.append(present.astype(float))
    return pd.concat(evidence, axis=1).mean(axis=1).mul(100.0)


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    frame = pd.read_csv(root / INPUT, sep=";", encoding="utf-8-sig", low_memory=False)
    rr = _num(frame, "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY")
    reliability = _num(frame, "SIM_RELIABILITY")
    selection = _num(frame, "HYPER_SCORE", "BALANCED_SCORE", "score", "score_effectif")
    light_pass = frame.get("SIM_SELECTION_SOURCE", pd.Series("", index=frame.index)).astype(str).str.contains("CI_LIGHT")
    selection = selection.where(selection.notna(), light_pass.map({True: 100.0, False: 50.0})).clip(0, 100)
    horizons = frame.get("horizon", frame.get("SIM_HORIZON", pd.Series("CT", index=frame.index))).astype(str).str.upper()
    thresholds = horizons.map(cfg["action_reward_risk_gate"]).fillna(cfg["action_reward_risk_gate"]["CT"])
    action = frame.get("asset_class", pd.Series("", index=frame.index)).astype(str).str.upper().eq("ACTION")
    frame["CHALLENGER_RR_GATE"] = (~action) | ((rr >= thresholds) & (reliability >= float(cfg["minimum_reliability"])))
    rr_score = (100.0 * rr / float(cfg["reward_risk_score_cap"])).clip(0, 100)
    weights = cfg["ranking_weights"]
    frame["CHALLENGER_RANK_SCORE"] = (
        float(weights["selection"]) * selection
        + float(weights["reward_risk"]) * rr_score
        + float(weights["reliability"]) * reliability
    ).round(2)
    downside = pd.concat([
        _lower_is_better_score(_num(frame, "risk_downside_beta_252d")),
        _lower_is_better_score(_num(frame, "max_drawdown_1y", "max_drawdown_1y_pct").abs()),
        _lower_is_better_score(_num(frame, "volatility_60d")),
    ], axis=1).mean(axis=1)
    frame["CHALLENGER_DOWNSIDE_SCORE"] = downside
    risk_weight = float(cfg["downside_risk_challenger"]["weight"])
    frame["CHALLENGER_RANK_SCORE_RISK_ADJUSTED"] = np.where(
        downside.notna(),
        (1.0 - risk_weight) * frame["CHALLENGER_RANK_SCORE"] + risk_weight * downside,
        frame["CHALLENGER_RANK_SCORE"],
    ).round(2)
    frame["CHALLENGER_SOURCE_CONFIDENCE"] = _source_confidence(frame).round(1)
    orientation = _orientation(frame)
    entry_threshold = orientation.map(cfg["entry_confidence_challenger"]).fillna(62.0)
    confidence = _num(frame, "CI_CONFIDENCE_SCORE_0_100", "entry_confidence")
    reference_state = frame.get("v22_2_entry_state", pd.Series(pd.NA, index=frame.index)).replace("", pd.NA)
    reference_state = reference_state.combine_first(frame.get("V22_2_1_ENTRY_STATE", pd.Series(pd.NA, index=frame.index)).replace("", pd.NA))
    reference_state = reference_state.combine_first(frame.get("SIM_STATUS", pd.Series("WAIT", index=frame.index))).fillna("WAIT").astype(str)
    watch = confidence.ge(entry_threshold) & orientation.ne("RISK_OFF") & ~reference_state.eq("READY_FOR_REVIEW")
    frame["CHALLENGER_ENTRY_THRESHOLD"] = entry_threshold
    frame["CHALLENGER_ENTRY_STATE"] = np.where(watch, "WATCH_WITH_TRIGGER", reference_state)
    frame["CHALLENGER_DOWNSIDE_RISK_STATUS"] = np.where(downside.notna(), "OBSERVED_RISK_ADJUSTMENT", "MISSING_NEUTRAL")
    frame["CHALLENGER_PORTFOLIO_BUDGET_STATUS"] = "POST_SELECTION_REQUIRED"
    frame["CHALLENGER_SHADOW_ONLY"] = True
    frame["CHALLENGER_REAL_ORDER_ALLOWED"] = False
    frame = frame.sort_values("CHALLENGER_RANK_SCORE_RISK_ADJUSTED", ascending=False, na_position="last")
    (root / OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    (root / AUDIT).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(root / OUTPUT, sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "status": "SUCCESS",
        "version": cfg["version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows": len(frame),
        "rr_gate_pass": int(frame["CHALLENGER_RR_GATE"].sum()),
        "watch_with_trigger": int(frame["CHALLENGER_ENTRY_STATE"].eq("WATCH_WITH_TRIGGER").sum()),
        "reference_modified": False,
        "portfolio_budget": cfg["portfolio_budget"],
        "promotion": cfg["promotion"],
        "shadow_only": True,
        "real_orders_enabled": False
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
