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
    common = ["name", "isin", "asset_class", "horizon", "score", "CI_CONFIDENCE_SCORE_V22_2_1", "CI_MARKET_ORIENTATION_EUROPE", "CI_SELECTION_GATE_STATUS_V4", "CI_SELECTION_GATE_REASON_V4", "CI_LIGHT_REASON", "CI_LIGHT_TRADINGVIEW_DAILY", "CI_LIGHT_TRADINGVIEW_WEEKLY", "CI_LIGHT_TRADINGVIEW_MONTHLY", "SIM_CURRENT_PRICE", "SIM_ENTRY_OPTIMAL", "SIM_TARGET_CENTRAL", "SIM_INVALIDATION", "SIM_CENTRAL_POTENTIAL_PCT_FROM_CURRENT", "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", "SIM_RELIABILITY", "CHALLENGER_RR_GATE", "CHALLENGER_RANK_SCORE", "CHALLENGER_DOWNSIDE_SCORE", "CHALLENGER_RANK_SCORE_RISK_ADJUSTED", "CHALLENGER_SOURCE_CONFIDENCE", "CHALLENGER_ENTRY_THRESHOLD", "CHALLENGER_ENTRY_STATE", "PORTFOLIO_BUDGET_DECISION", "PORTFOLIO_MAX_PAIR_CORRELATION", "PORTFOLIO_MAX_THEME_WEIGHT_PCT"]
    ci_path = root / "outputs/committee_master/CI_RESULTS_CHALLENGER_V2.csv"
    light_path = root / "outputs/committee_master/CI_LIGHT_RESULTS_CHALLENGER_V2.csv"
    _publish_csv(ci, ci_path, common)
    _publish_csv(light, light_path, common)
    md = root / "outputs/mobile/CI_AND_CI_LIGHT_CHALLENGER_V2.md"
    md.write_text("\n".join(["# Publication CI et CI LIGHT — approche Objectif/Risque challenger", "", f"Généré: {generated}", "", "La référence est inchangée; aucun ordre réel n'est autorisé.", "", "## CI", "", *_table(ci), "", "## CI LIGHT (processus autonome)", "", *_table(light), ""]), encoding="utf-8")
    payload = {"status": "SUCCESS", "generated_at_utc": generated, "ci_rows": len(ci), "ci_reference_selected": int(ci.get("CI_SELECTION_GATE_STATUS_V4", pd.Series(dtype=str)).eq("SELECTED").sum()), "ci_light_selected": len(light), "reference_modified": False, "real_orders_enabled": False, "outputs": [str(ci_path.relative_to(root)), str(light_path.relative_to(root)), str(md.relative_to(root))]}
    audit = root / "outputs/audit/CI_AND_CI_LIGHT_CHALLENGER_V2.json"
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
