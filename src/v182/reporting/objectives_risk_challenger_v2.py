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


def _reward_risk_score(rr: pd.Series, mapping: dict) -> pd.Series:
    target_ratio = float(mapping["target_ratio"])
    target_score = float(mapping["target_score"])
    cap_ratio = float(mapping["cap_ratio"])
    cap_score = float(mapping["cap_score"])
    lower = rr.mul(target_score / target_ratio)
    upper = target_score + (rr - target_ratio).mul((cap_score - target_score) / (cap_ratio - target_ratio))
    return lower.where(rr.le(target_ratio), upper).clip(0, cap_score)


def _risk_verdict(frame: pd.DataFrame, downside_score: pd.Series) -> pd.Series:
    verdict = pd.Series(pd.NA, index=frame.index, dtype="object")
    for name in ("RISK_VERDICT", "CI_RISK_VERDICT", "risk_verdict", "risk_state"):
        if name in frame:
            values = frame[name].astype(str).str.upper().str.strip()
            values = values.where(values.isin({"GREEN", "AMBER", "ORANGE", "RED"}))
            verdict = verdict.combine_first(values)
    inferred = pd.Series(
        np.select(
            [downside_score.ge(75), downside_score.ge(50), downside_score.ge(25), downside_score.notna()],
            ["GREEN", "AMBER", "ORANGE", "RED"],
            default="MISSING",
        ),
        index=frame.index,
    )
    return verdict.combine_first(inferred).fillna("MISSING")


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    frame = pd.read_csv(root / INPUT, sep=";", encoding="utf-8-sig", low_memory=False)
    rr = _num(frame, "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY")
    simulation_reliability = _num(frame, "SIM_RELIABILITY")
    confidence = _num(frame, "CI_CONFIDENCE_SCORE_0_100", "CI_CONFIDENCE_SCORE_V22_2_1", "entry_confidence")
    reliability = confidence
    selection = _num(frame, "HYPER_SCORE", "BALANCED_SCORE", "score", "score_effectif")
    selection = selection.clip(0, 100)
    horizons = frame.get("horizon", frame.get("SIM_HORIZON", pd.Series("CT", index=frame.index))).astype(str).str.upper()
    thresholds = horizons.map(cfg["action_reward_risk_gate"]).fillna(cfg["action_reward_risk_gate"]["CT"])
    action = frame.get("asset_class", pd.Series("", index=frame.index)).astype(str).str.upper().eq("ACTION")
    frame["CHALLENGER_RR_GATE"] = (~action) | ((rr >= thresholds) & (simulation_reliability >= float(cfg["minimum_reliability"])))
    rr_score = _reward_risk_score(rr, cfg["reward_risk_mapping"])
    weights = cfg["ranking_weights"]
    frame["OR_SELECTION_SCORE_0_100"] = selection.round(2)
    frame["OR_RR_SCORE_0_100"] = rr_score.round(2)
    frame["OR_RELIABILITY_0_100"] = reliability.round(2)
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
    risk_verdict = _risk_verdict(frame, downside)
    risk_multiplier = risk_verdict.map(cfg["risk_soft_multiplier"]).fillna(cfg["risk_soft_multiplier"]["MISSING"])
    frame["OR_RISK_VERDICT"] = risk_verdict
    frame["OR_RISK_SOFT_MULT"] = risk_multiplier
    frame["CHALLENGER_RANK_SCORE_RISK_ADJUSTED"] = (frame["CHALLENGER_RANK_SCORE"] * risk_multiplier).round(2)
    frame["OR_COMPOSITE_SHADOW"] = frame["CHALLENGER_RANK_SCORE_RISK_ADJUSTED"]
    frame["OR_FORMULA_VERSION"] = cfg["ranking_formula_version"]
    frame["CHALLENGER_SOURCE_CONFIDENCE"] = _source_confidence(frame).round(1)
    orientation = _orientation(frame)
    entry_threshold = orientation.map(cfg["entry_confidence_challenger"]).fillna(62.0)
    reference_state = frame.get("v22_2_entry_state", pd.Series(pd.NA, index=frame.index)).replace("", pd.NA)
    reference_state = reference_state.combine_first(frame.get("V22_2_1_ENTRY_STATE", pd.Series(pd.NA, index=frame.index)).replace("", pd.NA))
    reference_state = reference_state.combine_first(frame.get("SIM_STATUS", pd.Series("WAIT", index=frame.index))).fillna("WAIT").astype(str)
    watch = confidence.ge(entry_threshold) & orientation.ne("RISK_OFF") & ~reference_state.eq("READY_FOR_REVIEW")
    frame["CHALLENGER_ENTRY_THRESHOLD"] = entry_threshold
    frame["CHALLENGER_ENTRY_STATE"] = np.where(watch, "WATCH_WITH_TRIGGER", reference_state)
    minimum_confidence = float(cfg["buy_candidate_minimum_confidence"])
    labels = cfg["labels"]
    ready = (
        rr.ge(float(labels["ready_minimum_rr"]))
        & confidence.ge(float(labels["ready_minimum_confidence"]))
        & orientation.ne("RISK_OFF")
        & reference_state.eq("READY_FOR_REVIEW")
        & risk_verdict.isin({"GREEN", "AMBER"})
    )
    priority_watch = (
        ~reference_state.eq("READY_FOR_REVIEW")
        & rr.ge(float(labels["watch_priority_minimum_rr"]))
        & confidence.ge(float(labels["watch_priority_minimum_confidence"]))
        & orientation.ne("RISK_OFF")
    )
    upside = _num(frame, "SIM_CENTRAL_POTENTIAL_PCT_FROM_CURRENT", "CI_POTENTIAL_UPSIDE_PCT", "HYPER_POTENTIAL_PCT")
    watch = upside.gt(0) & confidence.ge(float(labels["watch_minimum_confidence"]))
    frame["OR_HEBDO_LABEL"] = np.select(
        [ready, priority_watch, watch],
        ["OR_READY_SHADOW", "OR_WATCH_PRIORITY", "OR_WATCH"],
        default="OR_HOLD_INSUFFICIENT",
    )
    frame["OR_BUY_CONFIDENCE_GATE"] = confidence.ge(minimum_confidence)
    frame["OR_AS_OF_UTC"] = datetime.now(timezone.utc).isoformat()
    as_of = pd.Series(pd.NA, index=frame.index, dtype="object")
    for field in ("as_of_close", "price_as_of", "market_data_as_of", "source_as_of"):
        if field in frame:
            as_of = as_of.combine_first(frame[field].replace("", pd.NA))
    frame["OR_AS_OF_CLOSE"] = as_of
    frame["OR_PROVENANCE_QUALITY"] = frame["CHALLENGER_SOURCE_CONFIDENCE"]
    frame["OR_DATA_CONTRACT_STATUS"] = np.where(
        selection.notna() & rr.notna() & confidence.notna() & risk_verdict.ne("MISSING"),
        "COMPLETE",
        "INCOMPLETE_FAIL_CLOSED",
    )
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
        "ranking_formula_version": cfg["ranking_formula_version"],
        "risk_soft_multiplier": cfg["risk_soft_multiplier"],
        "portfolio_budget": cfg["portfolio_budget"],
        "promotion": cfg["promotion"],
        "shadow_only": True,
        "real_orders_enabled": False
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
