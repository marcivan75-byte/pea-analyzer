from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from v182.decision import ipo_radar_v1_2 as v12
from v182.sources import euronext_ipo_v1_3, ipo_peers_v1_3

ROOT = v12.ROOT
legacy = v12.legacy
_BASE_EVALUATE = legacy.evaluate_candidates


def evaluate_candidates_v1_3(candidates: list[dict], config: dict, history: pd.DataFrame) -> list[dict]:
    api_key = os.environ.get("FINNHUB_API_KEY")
    enriched: list[dict] = []
    for original in candidates:
        candidate = dict(original)
        if candidate.get("opportunity_valuation_vs_peers") not in (None, ""):
            candidate.setdefault("peer_valuation_status", "PRESERVED_EXISTING_EVIDENCE")
        else:
            candidate = ipo_peers_v1_3.add_peer_evidence(candidate, api_key)
        enriched.append(candidate)
    return _BASE_EVALUATE(enriched, config, history)


def install_v1_3() -> None:
    v12.install_v1_2()
    legacy.collect_euronext = euronext_ipo_v1_3.collect_euronext_v1_3
    legacy.evaluate_candidates = evaluate_candidates_v1_3


def _v1_3_evidence(root: Path) -> dict:
    outdir = root / "outputs" / "ipo_radar"
    ranking_path = outdir / "IPO_RANKING.csv"
    evidence_path = outdir / "IPO_V1_3_EVIDENCE.csv"
    ranking = pd.read_csv(ranking_path, low_memory=False) if ranking_path.exists() else pd.DataFrame()
    columns = [
        "candidate_id", "identity_key", "name", "symbol", "isin", "exchange", "expected_date", "decision",
        "euronext_showcase_url", "euronext_detail_status", "euronext_icb_code", "euronext_icb_name",
        "euronext_website", "euronext_ipo_date_text", "euronext_ipo_price_text", "euronext_ipo_price",
        "euronext_ipo_currency", "euronext_ipo_type", "price_evidence_source",
        "sec_ipo_price_to_sales", "peer_valuation_status", "peer_valuation_source", "peer_grouping",
        "peer_count", "peer_symbols", "candidate_ps_annual", "peer_ps_annual_median",
        "candidate_to_peer_ps_ratio", "peer_valuation_detail", "opportunity_valuation_vs_peers",
        "risk_valuation", "live_order_allowed",
    ]
    if ranking.empty:
        evidence = pd.DataFrame(columns=columns)
    else:
        evidence = ranking.reindex(columns=columns).copy()
        evidence["live_order_allowed"] = False
    evidence.to_csv(evidence_path, index=False)
    euronext_enriched = 0
    peer_scored = 0
    if not evidence.empty:
        euronext_enriched = int((evidence["euronext_detail_status"].fillna("") == "SUCCESS").sum())
        peer_scored = int((evidence["peer_valuation_status"].fillna("") == "SUCCESS").sum())
    return {
        "evidence": "outputs/ipo_radar/IPO_V1_3_EVIDENCE.csv",
        "euronext_official_detail_enriched_count": euronext_enriched,
        "real_peer_valuation_scored_count": peer_scored,
        "peer_minimum_valid_count": 3,
        "peer_multiple_basis": "ANNUAL_PRICE_TO_SALES_ONLY",
    }


def run(root: Path = ROOT) -> dict:
    install_v1_3()
    summary = v12.run(root)
    evidence = _v1_3_evidence(root)
    summary["module_version"] = "IPO_RADAR_V1.3"
    summary["evidence_layer"] = "EURONEXT_OFFICIAL_SHOWCASE_PLUS_FINNHUB_REAL_PEERS_V1.3"
    summary["euronext_evidence_policy"] = "OFFICIAL_SHOWCASE_FIELDS_ONLY_NO_SECTOR_OR_PEA_INFERENCE"
    summary["peer_valuation_policy"] = "REAL_PEERS_SAME_BASIS_ANNUAL_PS_MEDIAN_MIN_3_OTHERWISE_MISSING"
    summary["peer_valuation_score_policy"] = "OPPORTUNITY_AND_INVERSE_RISK_ONLY_WHEN_REAL_PEER_GATE_PASSES"
    summary["v1_3_evidence"] = evidence
    summary.setdefault("outputs", {})["v1_3_evidence"] = evidence["evidence"]
    summary["live_orders_enabled"] = False
    summary["can_create_buy"] = False
    summary_path = root / "outputs" / "ipo_radar" / "IPO_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
