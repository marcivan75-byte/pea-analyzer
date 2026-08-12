from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

from v182.decision.committee_master import sector_ranking
from v182.decision.etf_mt_committee_v2082 import apply as apply_etf_mt_v2082
from v182.reporting import committee_master_run

ROOT = Path(__file__).resolve().parents[3]


def _gold_rows(payload: dict) -> pd.DataFrame:
    rows = []
    for horizon, spec in (payload.get("current_scores") or {}).items():
        rows.append({
            "asset_class": "GOLD", "horizon": horizon, "isin": "", "name": "OR", "sector": "METAUX PRECIEUX",
            "score": spec.get("score"), "coverage_pct": spec.get("coverage_pct", 0.0), "status": spec.get("status", "BLOCK_DATA"),
            "decision": spec.get("decision", "ABSTAIN"), "active_criteria": spec.get("active_criteria", 102), "available_criteria": spec.get("available_criteria", 0),
            "score_source": payload.get("version", "GOLD_V1_1"), "backtest_attribution": "No V1.1 performance claim until dedicated PIT backtest.",
            "notes": "OR autonomous hors PEA; XAU/EUR primary construction; T1/T2 forbidden; SHADOW_RESEARCH_ONLY; no real orders.",
            "qds_or": payload.get("QDS_OR"), "data_trust_or": payload.get("DATA_TRUST_OR"), "gold_primary_price": payload.get("primary_price"),
        })
    return pd.DataFrame(rows)


def run(root: Path = ROOT) -> dict:
    summary = committee_master_run.run(root)
    summary = apply_etf_mt_v2082(root, summary)
    outdir = root / "outputs" / "committee_master"
    gold_path = root / "outputs" / "gold_v1_1" / "GOLD_V1_1_DECISION.json"
    if not gold_path.exists():
        summary["gold_v1_1"] = {"status": "BLOCK_DATA", "reason": "GOLD_V1_1_DECISION.json missing"}
        summary["notes"] = [n for n in summary.get("notes", []) if "Gold remains blocked" not in n]
        summary["notes"].append("Gold V1.1 registry is present, but the current data run did not produce a decision snapshot; no score is fabricated.")
        (outdir / "SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return summary

    payload = json.loads(gold_path.read_text(encoding="utf-8"))
    if int(payload.get("criteria_count", 0)) != 102:
        raise RuntimeError("GOLD_RUNTIME_CRITERIA_COUNT_NOT_102")

    decisions_path = outdir / "COMMITTEE_DECISIONS.csv"
    decisions = pd.read_csv(decisions_path, sep=";", encoding="utf-8-sig", low_memory=False)
    decisions = decisions[decisions["asset_class"].astype(str).str.upper() != "GOLD"].copy()
    gold = _gold_rows(payload)
    if "generated_at_utc" in decisions.columns: gold["generated_at_utc"] = payload.get("generated_at_utc")
    if "live_orders_enabled" in decisions.columns: gold["live_orders_enabled"] = False
    decisions = pd.concat([decisions, gold], ignore_index=True, sort=False)
    decisions.to_csv(decisions_path, sep=";", index=False, encoding="utf-8-sig")
    sector_ranking(decisions).to_csv(outdir / "SECTOR_RANKING.csv", sep=";", index=False, encoding="utf-8-sig")

    summary["status_counts"] = decisions.groupby(["asset_class", "horizon", "status"], dropna=False).size().reset_index(name="count").to_dict("records")
    summary["decision_counts"] = decisions.groupby(["asset_class", "horizon", "decision"], dropna=False).size().reset_index(name="count").to_dict("records")
    summary["registry_integrity"]["gold_reference_present"] = True
    summary["registry_integrity"]["gold_criteria_expected"] = 102
    summary["registry_integrity"]["gold_criteria_loaded"] = int(payload.get("criteria_count", 0))
    summary["gold_v1_1"] = {
        "status": payload.get("status"), "criteria_count": payload.get("criteria_count"), "contribution_blocks": payload.get("contribution_blocks"),
        "GOLD_SCORE_CT": payload.get("GOLD_SCORE_CT"), "GOLD_SCORE_MT": payload.get("GOLD_SCORE_MT"), "QDS_OR": payload.get("QDS_OR"), "DATA_TRUST_OR": payload.get("DATA_TRUST_OR"),
        "hard_gates": payload.get("hard_gates"), "top_factors": payload.get("top_factors"), "source_status": payload.get("source_status"),
        "performance_attribution": "NONE_FOR_V1_1_UNTIL_DEDICATED_PIT_BACKTEST",
    }
    summary["outputs"]["gold_decision"] = "outputs/gold_v1_1/GOLD_V1_1_DECISION.json"
    summary["outputs"]["gold_criteria"] = "outputs/gold_v1_1/GOLD_V1_1_CRITERIA.csv"
    summary["outputs"]["gold_sources"] = "outputs/gold_v1_1/GOLD_V1_1_SOURCE_STATUS.csv"
    summary["outputs"]["gold_history"] = "state/GOLD_V1_1_SHADOW_HISTORY.csv"
    summary["notes"] = [n for n in summary.get("notes", []) if "Gold remains blocked" not in n]
    summary["notes"].extend([
        "ETF MT V20.8.2 replaces missing-weight blocking at Committee level with available-criterion renormalization to 100%, subject to 70% weighted coverage; it has no historical performance attribution yet.",
        "Gold V1.1 is integrated as autonomous SHADOW/RESEARCH_ONLY scoring outside PEA.",
        "Gold top-level tactical 45/25/20/10 and strategic 25/20/20/15/10/10 families are source-preserved; reconstructed intra-block weights are provisional and not performance-optimised.",
        "Gold missing official WGC/central-bank/consensus observations remain MISSING; no neutral 50/100 imputation.",
        "Gold V1.1 has no performance attribution until a dedicated PIT backtest is completed.",
    ])
    (outdir / "SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary
