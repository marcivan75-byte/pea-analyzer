from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.decision import ipo_radar_v1_3 as v13
from v182.sources import euronext_ipo_news_v1_4

ROOT = v13.ROOT
legacy = v13.legacy
_BASE_EURONEXT = v13.euronext_ipo_v1_3.collect_euronext_v1_3


def collect_euronext_v1_4(start, end, timeout: int = 20):
    rows, status = _BASE_EURONEXT(start, end, timeout)
    enriched: list[dict] = []
    news_success = 0
    news_matches = 0
    for row in rows:
        candidate = euronext_ipo_news_v1_4.enrich_candidate(dict(row), timeout=min(timeout, 15))
        if candidate.get("euronext_news_discovery_status") == "SUCCESS":
            news_matches += 1
        if int(candidate.get("euronext_news_fetch_success_count") or 0) > 0:
            news_success += 1
        enriched.append(candidate)
    status = dict(status)
    status["regulated_news_matched_count"] = news_matches
    status["regulated_news_enriched_count"] = news_success
    status["regulated_news_policy"] = "SHADOW_FACTS_ONLY_NO_ACTIVE_SCORE_V1.4"
    return enriched, status


def install_v1_4() -> None:
    v13.install_v1_3()
    # v13.run() calls install_v1_3() again. Patch the module-level collector too,
    # so the later reinstall still resolves to the V1.4 wrapper rather than
    # silently reverting to the V1.3 showcase-only collector.
    v13.euronext_ipo_v1_3.collect_euronext_v1_3 = collect_euronext_v1_4
    legacy.collect_euronext = collect_euronext_v1_4


def _write_news_evidence(root: Path) -> dict:
    outdir = root / "outputs" / "ipo_radar"
    ranking_path = outdir / "IPO_RANKING.csv"
    output_path = outdir / "IPO_EURONEXT_NEWS_EVIDENCE_V1_4.csv"
    ranking = pd.read_csv(ranking_path, low_memory=False) if ranking_path.exists() else pd.DataFrame()
    columns = [
        "candidate_id", "identity_key", "name", "symbol", "isin", "exchange", "expected_date", "decision",
        "euronext_showcase_url", "euronext_icb_code", "euronext_icb_name", "euronext_ipo_type",
        "euronext_news_discovery_status", "euronext_news_discovery_error", "euronext_news_count",
        "euronext_news_urls", "euronext_news_fetch_success_count", "euronext_news_fetch_errors",
        "euronext_news_gross_proceeds_local", "euronext_news_gross_proceeds_currency",
        "euronext_news_gross_proceeds_evidence", "euronext_news_offer_price_local",
        "euronext_news_offer_price_currency", "euronext_news_offer_price_evidence",
        "euronext_news_cornerstone_amount_local", "euronext_news_cornerstone_currency",
        "euronext_news_cornerstone_evidence", "euronext_news_new_shares", "euronext_news_issued_shares",
        "euronext_news_demand_signal_shadow", "euronext_news_primary_offer_detected",
        "euronext_news_secondary_offer_detected", "euronext_news_retail_offer_detected",
        "euronext_news_management_commitment_detected", "euronext_news_cornerstone_detected",
        "euronext_news_prospectus_reference_detected", "euronext_news_information_document_reference_detected",
        "euronext_news_document_urls", "euronext_news_evidence_policy", "live_order_allowed",
    ]
    if ranking.empty:
        evidence = pd.DataFrame(columns=columns)
    else:
        evidence = ranking.reindex(columns=columns).copy()
        evidence["live_order_allowed"] = False
    evidence.to_csv(output_path, index=False)
    matched = 0
    fetched = 0
    strong = 0
    prospectus_refs = 0
    if not evidence.empty:
        matched = int((evidence["euronext_news_discovery_status"].fillna("") == "SUCCESS").sum())
        fetched = int((pd.to_numeric(evidence["euronext_news_fetch_success_count"], errors="coerce").fillna(0) > 0).sum())
        strong = int((evidence["euronext_news_demand_signal_shadow"].fillna("") == "STRONG_DEMAND").sum())
        prospectus_refs = int(evidence["euronext_news_prospectus_reference_detected"].fillna(False).astype(bool).sum())
    return {
        "evidence": "outputs/ipo_radar/IPO_EURONEXT_NEWS_EVIDENCE_V1_4.csv",
        "matched_candidate_count": matched,
        "fetched_candidate_count": fetched,
        "strong_demand_shadow_count": strong,
        "prospectus_reference_count": prospectus_refs,
        "active_score_influence": 0.0,
    }


def run(root: Path = ROOT) -> dict:
    install_v1_4()
    summary = v13.run(root)
    news = _write_news_evidence(root)
    summary["module_version"] = "IPO_RADAR_V1.4"
    summary["regulated_news_layer"] = "EURONEXT_GDELT_DISCOVERY_OFFICIAL_PAGE_FACTS_V1.4"
    summary["regulated_news_policy"] = "SHADOW_FACTS_ONLY_NO_ACTIVE_SCORE_NO_BUY"
    summary["regulated_news_evidence"] = news
    summary.setdefault("outputs", {})["euronext_news_evidence_v1_4"] = news["evidence"]
    summary["live_orders_enabled"] = False
    summary["can_create_buy"] = False
    summary_path = root / "outputs" / "ipo_radar" / "IPO_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
