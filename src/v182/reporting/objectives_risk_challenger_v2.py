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
    return verdict.fillna("MISSING")


def _first_text(frame: pd.DataFrame, *names: str, default: str = "") -> pd.Series:
    result = pd.Series(pd.NA, index=frame.index, dtype="object")
    for name in names:
        if name in frame:
            values = frame[name].replace("", pd.NA)
            result = result.combine_first(values)
    return result.fillna(default).astype(str).str.upper().str.strip()


def _attach_etf_mt_context(frame: pd.DataFrame, root: Path, cfg: dict) -> pd.DataFrame:
    path = root / "outputs/etf_mt_v2081/V20.8.2_ETF_MT_DYNAMIC_RANKING.csv"
    if not path.exists() or not path.stat().st_size:
        frame["OR_ETF_MT_DATA_STATUS"] = np.where(
            frame.get("asset_class", pd.Series("", index=frame.index)).astype(str).str.upper().eq("ETF"),
            "MISSING_FAIL_CLOSED",
            "NOT_APPLICABLE",
        )
        return frame
    dynamic = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    identifier = "isin" if "isin" in dynamic else "instrument_id" if "instrument_id" in dynamic else None
    if identifier is None:
        frame["OR_ETF_MT_DATA_STATUS"] = "MISSING_FAIL_CLOSED"
        return frame
    wanted = [
        identifier, "history_sessions", "staleness_days", "criteria_complete", "dynamic_decision",
        "dynamic_score_final", "dynamic_weight_coverage_pct", "dynamic_available_criteria",
        *cfg["etf_mt_shadow"]["technical_quality_fields"], *cfg["etf_mt_shadow"]["maximum_drawdown_fields"],
    ]
    context = dynamic[[field for field in dict.fromkeys(wanted) if field in dynamic]].copy()
    context = context.rename(columns={identifier: "isin"}).drop_duplicates("isin", keep="last")
    frame = frame.merge(context, on="isin", how="left", suffixes=("", "_etf_mt"))
    etf = frame.get("asset_class", pd.Series("", index=frame.index)).astype(str).str.upper().eq("ETF")
    history = _num(frame, "history_sessions")
    stale = _num(frame, "staleness_days")
    decision = _first_text(frame, "dynamic_decision", default="BLOCK_DATA")
    eligible = (
        history.ge(float(cfg["etf_mt_shadow"]["minimum_history_sessions"]))
        & stale.le(float(cfg["etf_mt_shadow"]["maximum_staleness_days"]))
        & decision.ne("BLOCK_DATA")
    )
    frame["OR_ETF_MT_DATA_STATUS"] = np.where(~etf, "NOT_APPLICABLE", np.where(eligible, "ELIGIBLE_SHADOW", "BLOCK_DATA"))
    quality_parts = []
    for field in cfg["etf_mt_shadow"]["technical_quality_fields"]:
        if field in frame:
            quality_parts.append(pd.to_numeric(frame[field], errors="coerce").rank(pct=True).mul(100.0))
    drawdown = _num(frame, *cfg["etf_mt_shadow"]["maximum_drawdown_fields"]).abs()
    if drawdown.notna().any():
        quality_parts.append(_lower_is_better_score(drawdown))
    frame["OR_ETF_MT_TECHNICAL_QUALITY"] = pd.concat(quality_parts, axis=1).mean(axis=1) if quality_parts else np.nan
    complete = _first_text(frame, "criteria_complete", default="FALSE").isin({"TRUE", "1", "YES"}).astype(float)
    freshness = (1.0 - stale.div(30.0)).clip(0, 1)
    history_quality = history.div(float(cfg["etf_mt_shadow"]["reliability_history_cap_sessions"])).clip(0, 1)
    frame["OR_ETF_MT_DATA_RELIABILITY"] = pd.concat([complete, freshness, history_quality], axis=1).mean(axis=1).mul(100).round(2)
    secondary = [field for field in ("boursorama_alpha_1y", "aum", "risk_beta_252d", "morningstar_rating") if field in frame]
    frame["OR_ETF_SECONDARY_CONTEXT_COVERAGE"] = (
        frame[secondary].notna().mean(axis=1).mul(100.0).round(1) if secondary else np.nan
    )
    return frame


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    frame = pd.read_csv(root / INPUT, sep=";", encoding="utf-8-sig", low_memory=False)
    frame = _attach_etf_mt_context(frame, root, cfg)
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
    frame["OR_DOWNSIDE_RISK_PROXY_SCORE"] = downside
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
    actions = cfg["hebdo_entry_action"]
    committee_state = _first_text(
        frame, "CI_SELECTION_GATE_STATUS_V4", "committee_data_status", "decision", default=""
    )
    entry_proof = _first_text(
        frame, "v22_2_entry_state", "V22_2_1_ENTRY_STATE", "SIM_STATUS", default="INSUFFICIENT_ENTRY_PROOF"
    )
    etf_regime = _first_text(
        frame, "ETF_REGIME", "etf_regime", "market_regime", "CI_MARKET_REGIME", default="MISSING"
    )
    overextended = _first_text(frame, "overextension_state", "extension_state", default="").str.contains(
        "OVEREXT|EXTENDED", regex=True
    )
    block_data = committee_state.str.contains("BLOCK_DATA", regex=False)
    insufficient = entry_proof.str.contains("INSUFFICIENT", regex=False)
    etf_adverse = frame.get("asset_class", pd.Series("", index=frame.index)).astype(str).str.upper().eq("ETF") & etf_regime.ne(
        str(actions["etf_required_regime"]).upper()
    )
    etf_data_block = frame.get("OR_ETF_MT_DATA_STATUS", pd.Series("NOT_APPLICABLE", index=frame.index)).eq("BLOCK_DATA")
    non_actionable = (
        block_data
        | etf_data_block
        | etf_adverse
        | rr.lt(float(actions["non_actionable_maximum_rr"]))
        | confidence.lt(float(actions["minimum_confidence"]))
        | selection.isna()
        | rr.isna()
        | confidence.isna()
    )
    pullback = (
        insufficient
        | overextended
        | risk_verdict.isin(set(actions["pullback_risk_verdicts"]))
        | risk_verdict.eq("MISSING")
    )
    ready_action = (
        rr.ge(float(actions["ready_minimum_rr"]))
        & confidence.ge(float(actions["ready_minimum_confidence"]))
        & risk_verdict.isin(set(actions["ready_risk_verdicts"]))
        & ~pullback
        & ~non_actionable
    )
    watch_action = rr.ge(float(actions["watch_minimum_rr"])) & ~pullback & ~non_actionable
    frame["OR_ENTRY_ACTION_SHADOW"] = np.select(
        [non_actionable, pullback, ready_action, watch_action],
        ["NON_ACTIONNABLE_SHADOW", "ATTENDRE_REPLI_SHADOW", "READY_RESEARCH_ONLY", "SURVEILLER_SHADOW"],
        default="NON_ACTIONNABLE_SHADOW",
    )
    frame["OR_HEBDO_GATE_REASON"] = np.select(
        [block_data, etf_data_block, etf_adverse, insufficient, risk_verdict.eq("MISSING"), risk_verdict.isin({"AMBER", "ORANGE"}), overextended],
        ["BLOCK_DATA", "ETF_MT_BLOCK_DATA", "ETF_REGIME_ADVERSE_OR_MISSING", "INSUFFICIENT_ENTRY_PROOF", "RISK_MISSING", "RISK_SOFT_CAP", "OVEREXTENSION"],
        default="NONE",
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
        "entry_action_distribution": frame["OR_ENTRY_ACTION_SHADOW"].value_counts(dropna=False).to_dict(),
        "data_contract_distribution": frame["OR_DATA_CONTRACT_STATUS"].value_counts(dropna=False).to_dict(),
        "etf_mt_data_distribution": frame["OR_ETF_MT_DATA_STATUS"].value_counts(dropna=False).to_dict(),
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
