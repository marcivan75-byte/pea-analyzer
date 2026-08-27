from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]


def _read(root: Path, relative: str) -> pd.DataFrame:
    path = root / relative
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False) if path.exists() and path.stat().st_size else pd.DataFrame()


def _merge(base: pd.DataFrame, challenger: pd.DataFrame) -> pd.DataFrame:
    fields = [
        "isin", "SIM_CURRENT_PRICE", "SIM_ENTRY_OPTIMAL", "SIM_TARGET_CENTRAL", "SIM_INVALIDATION",
        "SIM_CENTRAL_POTENTIAL_PCT_FROM_CURRENT", "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", "SIM_RELIABILITY",
        "CHALLENGER_RR_GATE", "CHALLENGER_RANK_SCORE", "CHALLENGER_ENTRY_THRESHOLD", "CHALLENGER_ENTRY_STATE",
        "CHALLENGER_DOWNSIDE_SCORE", "CHALLENGER_RANK_SCORE_RISK_ADJUSTED", "CHALLENGER_SOURCE_CONFIDENCE",
        "OR_SELECTION_SCORE_0_100", "OR_RR_SCORE_0_100", "OR_RELIABILITY_0_100", "OR_RISK_VERDICT",
        "OR_RISK_SOFT_MULT", "OR_COMPOSITE_SHADOW", "OR_FORMULA_VERSION", "OR_HEBDO_LABEL",
        "OR_BUY_CONFIDENCE_GATE", "OR_AS_OF_UTC", "OR_AS_OF_CLOSE", "OR_PROVENANCE_QUALITY",
        "OR_DATA_CONTRACT_STATUS", "OR_ENTRY_ACTION_SHADOW", "OR_HEBDO_GATE_REASON",
        "OR_ETF_MT_DATA_STATUS", "OR_ETF_MT_TECHNICAL_QUALITY", "OR_ETF_MT_DATA_RELIABILITY",
        "OR_ETF_SECONDARY_CONTEXT_COVERAGE",
        "OR_VALID_SOURCE_COUNT", "OR_CRITICAL_SOURCE_FAILURE_COUNT", "OR_WEEKLY_SOURCE_GATE",
        "OR_SOURCE_RELIABILITY_0_100", "OR_SOURCE_RELIABILITY_INFLUENCE",
    ]
    available = [field for field in fields if field in challenger]
    return base.merge(challenger[available].drop_duplicates("isin"), on="isin", how="left")


def _publish_csv(frame: pd.DataFrame, path: Path, preferred: list[str]) -> None:
    columns = [field for field in preferred if field in frame]
    path.parent.mkdir(parents=True, exist_ok=True)
    frame[columns].to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def _table(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return ["Aucun instrument."]
    cols = ["name", "asset_class", "score", "CI_CONFIDENCE_SCORE_V22_2_1", "SIM_CURRENT_PRICE", "SIM_ENTRY_OPTIMAL", "SIM_TARGET_CENTRAL", "SIM_INVALIDATION", "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", "SIM_RELIABILITY", "CHALLENGER_RANK_SCORE", "CI_SELECTION_GATE_STATUS_V4", "CI_LIGHT_REASON"]
    cols = [c for c in cols if c in frame]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in frame.head(20).iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("nan", "INDISPONIBLE") for c in cols) + " |")
    return lines


def run(root: Path = ROOT) -> dict:
    generated = datetime.now(timezone.utc).isoformat()
    config_path = root / "config/OBJECTIVES_RISK_CHALLENGER_V2.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    weekly_gate = config.get("weekly_source_gate", {})
    challenger = _read(root, "outputs/committee_master/OBJECTIVES_RISK_CHALLENGER_V2.csv")
    portfolio = _read(root, "outputs/committee_master/PORTFOLIO_BUDGET_CHALLENGER_V2.csv")
    ci = _merge(_read(root, "outputs/committee_master/CI_SELECTION_ALL_V4.csv"), challenger)
    light = _merge(_read(root, "outputs/committee_master/CI_LIGHT_V4.csv"), challenger)
    portfolio_fields = [field for field in ("isin", "PORTFOLIO_BUDGET_DECISION", "PORTFOLIO_MAX_PAIR_CORRELATION", "PORTFOLIO_MAX_THEME_WEIGHT_PCT") if field in portfolio]
    if portfolio_fields:
        ci = ci.merge(portfolio[portfolio_fields].drop_duplicates("isin"), on="isin", how="left")
        light = light.merge(portfolio[portfolio_fields].drop_duplicates("isin"), on="isin", how="left")
    if "CHALLENGER_RANK_SCORE" in ci:
        ci = ci.sort_values("CHALLENGER_RANK_SCORE", ascending=False, na_position="last")
    if "CHALLENGER_RANK_SCORE" in light:
        light = light.sort_values("CHALLENGER_RANK_SCORE", ascending=False, na_position="last")
    common = ["name", "isin", "asset_class", "horizon", "score", "CI_CONFIDENCE_SCORE_V22_2_1", "CI_MARKET_ORIENTATION_EUROPE", "CI_SELECTION_GATE_STATUS_V4", "CI_SELECTION_GATE_REASON_V4", "CI_LIGHT_REASON", "CI_LIGHT_TRADINGVIEW_DAILY", "CI_LIGHT_TRADINGVIEW_WEEKLY", "CI_LIGHT_TRADINGVIEW_MONTHLY", "SIM_CURRENT_PRICE", "SIM_ENTRY_OPTIMAL", "SIM_TARGET_CENTRAL", "SIM_INVALIDATION", "SIM_CENTRAL_POTENTIAL_PCT_FROM_CURRENT", "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", "SIM_RELIABILITY", "CHALLENGER_RR_GATE", "CHALLENGER_RANK_SCORE", "CHALLENGER_DOWNSIDE_SCORE", "CHALLENGER_RANK_SCORE_RISK_ADJUSTED", "CHALLENGER_SOURCE_CONFIDENCE", "CHALLENGER_ENTRY_THRESHOLD", "CHALLENGER_ENTRY_STATE", "OR_SELECTION_SCORE_0_100", "OR_RR_SCORE_0_100", "OR_RELIABILITY_0_100", "OR_RISK_VERDICT", "OR_RISK_SOFT_MULT", "OR_COMPOSITE_SHADOW", "OR_FORMULA_VERSION", "OR_HEBDO_LABEL", "OR_BUY_CONFIDENCE_GATE", "OR_AS_OF_UTC", "OR_AS_OF_CLOSE", "OR_PROVENANCE_QUALITY", "OR_DATA_CONTRACT_STATUS", "OR_ENTRY_ACTION_SHADOW", "OR_HEBDO_GATE_REASON", "PORTFOLIO_BUDGET_DECISION", "PORTFOLIO_MAX_PAIR_CORRELATION", "PORTFOLIO_MAX_THEME_WEIGHT_PCT"]
    ci_path = root / "outputs/committee_master/CI_RESULTS_CHALLENGER_V2.csv"
    light_path = root / "outputs/committee_master/CI_LIGHT_RESULTS_CHALLENGER_V2.csv"
    _publish_csv(ci, ci_path, common)
    _publish_csv(light, light_path, common)
    publication_date = datetime.now(timezone.utc).date().isoformat()
    or_fields = [
        "name", "isin", "asset_class", "horizon", "SIM_CURRENT_PRICE", "SIM_ENTRY_OPTIMAL",
        "SIM_TARGET_CENTRAL", "SIM_INVALIDATION", "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", "SIM_RELIABILITY",
        "CI_CONFIDENCE_SCORE_0_100", "CI_CONFIDENCE_SCORE_V22_2_1", "OR_SELECTION_SCORE_0_100",
        "OR_RR_SCORE_0_100", "OR_RELIABILITY_0_100", "OR_RISK_VERDICT", "OR_RISK_SOFT_MULT",
        "OR_COMPOSITE_SHADOW", "OR_HEBDO_LABEL", "OR_BUY_CONFIDENCE_GATE", "OR_FORMULA_VERSION",
        "OR_AS_OF_UTC", "OR_AS_OF_CLOSE", "OR_PROVENANCE_QUALITY", "OR_DATA_CONTRACT_STATUS",
        "OR_ENTRY_ACTION_SHADOW", "OR_HEBDO_GATE_REASON", "OR_ETF_MT_DATA_STATUS",
        "OR_ETF_MT_TECHNICAL_QUALITY", "OR_ETF_MT_DATA_RELIABILITY",
        "OR_ETF_SECONDARY_CONTEXT_COVERAGE", "SIM_SELECTION_SOURCE",
        "OR_VALID_SOURCE_COUNT", "OR_CRITICAL_SOURCE_FAILURE_COUNT", "OR_WEEKLY_SOURCE_GATE",
        "OR_SOURCE_RELIABILITY_0_100",
    ]
    or_combined = challenger.sort_values("OR_COMPOSITE_SHADOW", ascending=False, na_position="last") if "OR_COMPOSITE_SHADOW" in challenger else challenger
    or_etf = or_combined[or_combined.get("asset_class", pd.Series("", index=or_combined.index)).astype(str).str.upper().eq("ETF")]
    or_action_ct = or_combined[
        or_combined.get("asset_class", pd.Series("", index=or_combined.index)).astype(str).str.upper().eq("ACTION")
        & or_combined.get("horizon", or_combined.get("SIM_HORIZON", pd.Series("", index=or_combined.index))).astype(str).str.upper().eq("CT")
    ]
    or_path = root / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_{publication_date}.csv"
    or_combined_path = root / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_COMBINED_{publication_date}.csv"
    or_etf_path = root / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_ETF_ONLY_{publication_date}.csv"
    or_action_path = root / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_ACTION_CT_ONLY_{publication_date}.csv"
    or_etf_mt_path = root / f"outputs/committee_master/OR_RANKING_ETF_MT_SHADOW_{publication_date}.csv"
    eligible = or_combined[or_combined.get("OR_WEEKLY_SOURCE_GATE", pd.Series("AUDIT_ONLY_FAIL_CLOSED", index=or_combined.index)).eq("PASS")]
    top_n = int(weekly_gate.get("top_n_per_asset_class", 15))
    forward_sessions = tuple(int(value) for value in weekly_gate.get("forward_validation_sessions", [5, 10, 20]))
    top_action = eligible[eligible.get("asset_class", pd.Series("", index=eligible.index)).astype(str).str.upper().eq("ACTION")].head(top_n)
    top_etf = eligible[eligible.get("asset_class", pd.Series("", index=eligible.index)).astype(str).str.upper().eq("ETF")].head(top_n)
    top_action_path = root / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_TOP{top_n}_ACTION_{publication_date}.csv"
    top_etf_path = root / f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_TOP{top_n}_ETF_{publication_date}.csv"
    _publish_csv(or_combined, or_path, or_fields)
    _publish_csv(or_combined, or_combined_path, or_fields)
    _publish_csv(or_etf, or_etf_path, or_fields)
    _publish_csv(or_action_ct, or_action_path, or_fields)
    _publish_csv(or_etf, or_etf_mt_path, or_fields)
    _publish_csv(top_action, top_action_path, or_fields)
    _publish_csv(top_etf, top_etf_path, or_fields)
    forward_path = root / "state/objectives_risk/OR_HEBDO_FORWARD_VALIDATION_SCHEDULE.csv"
    forward_path.parent.mkdir(parents=True, exist_ok=True)
    schedule_rows = []
    generated_day = pd.Timestamp(publication_date)
    forward_universe = pd.concat([top_action, top_etf], ignore_index=True, sort=False)
    for _, row in forward_universe.iterrows():
        for sessions in forward_sessions:
            schedule_rows.append({
                "snapshot_date": publication_date, "isin": row.get("isin"), "asset_class": row.get("asset_class"),
                "or_composite_shadow": row.get("OR_COMPOSITE_SHADOW"), "entry_price": row.get("SIM_CURRENT_PRICE"),
                "forward_sessions": sessions, "evaluation_not_before": (generated_day + pd.offsets.BDay(sessions)).date().isoformat(),
                "status": "PENDING", "research_only": True,
            })
    schedule_columns = [
        "snapshot_date", "isin", "asset_class", "or_composite_shadow", "entry_price",
        "forward_sessions", "evaluation_not_before", "status", "research_only",
    ]
    schedule = pd.DataFrame(schedule_rows, columns=schedule_columns)
    if forward_path.exists() and forward_path.stat().st_size:
        try:
            previous_schedule = pd.read_csv(forward_path, sep=";")
        except pd.errors.EmptyDataError:
            previous_schedule = pd.DataFrame(columns=schedule_columns)
        schedule = pd.concat([previous_schedule, schedule], ignore_index=True, sort=False)
    if not schedule.empty:
        schedule = schedule.drop_duplicates(["snapshot_date", "isin", "forward_sessions"], keep="first")
    schedule.to_csv(forward_path, sep=";", index=False, encoding="utf-8-sig")
    md = root / "outputs/mobile/CI_AND_CI_LIGHT_CHALLENGER_V2.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("\n".join(["# Publication CI et CI LIGHT — approche Objectif/Risque challenger", "", f"Généré: {generated}", "", "La référence est inchangée; aucun ordre réel n'est autorisé.", "", "## CI", "", *_table(ci), "", "## CI LIGHT (processus autonome)", "", *_table(light), ""]), encoding="utf-8")
    payload = {"status": "SUCCESS", "generated_at_utc": generated, "ci_rows": len(ci), "ci_reference_selected": int(ci.get("CI_SELECTION_GATE_STATUS_V4", pd.Series(dtype=str)).eq("SELECTED").sum()), "ci_light_selected": len(light), "or_rows": len(or_combined), "or_etf_rows": len(or_etf), "or_action_ct_rows": len(or_action_ct), "or_source_gate_pass": len(eligible), "or_top15_action_rows": len(top_action), "or_top15_etf_rows": len(top_etf), "forward_validation_pending": len(schedule), "reference_modified": False, "real_orders_enabled": False, "outputs": [str(path.relative_to(root)) for path in (ci_path, light_path, md, or_path, or_combined_path, or_etf_path, or_action_path, or_etf_mt_path, top_action_path, top_etf_path, forward_path)]}
    audit = root / "outputs/audit/CI_AND_CI_LIGHT_CHALLENGER_V2.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
