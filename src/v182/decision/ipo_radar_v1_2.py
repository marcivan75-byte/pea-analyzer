from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.decision import ipo_radar_stabilized_v1_1 as v11
from v182.sources import sec_ipo_deep_v1_2 as deep_sec

ROOT = v11.ROOT
legacy = v11.legacy
_BASE_MARKET_READINESS = legacy.market_readiness_score


def market_readiness_v1_2(row: dict) -> float:
    """Preserve the V1.1 readiness semantics for the deeper V1.2 prospectus parser."""
    adjusted = dict(row)
    if str(adjusted.get("sec_analysis_status", "")).startswith("PROSPECTUS_DEEP_PARSED"):
        adjusted["sec_analysis_status"] = "PROSPECTUS_PARSED"
    return _BASE_MARKET_READINESS(adjusted)


def install_v1_2() -> None:
    v11.install_stabilization()
    legacy.sec_ipo.enrich_candidate = deep_sec.enrich_candidate
    legacy.market_readiness_score = market_readiness_v1_2


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def _warning_flags(row: dict) -> list[str]:
    warnings: list[str] = []
    valuation_risk = _as_float(row.get("shadow_absolute_valuation_risk"))
    dilution_pct = _as_float(row.get("sec_dilution_pct"))
    runway = _as_float(row.get("sec_cash_runway_years_post_ipo_upper_bound"))
    confidence = _as_float(row.get("sec_offer_terms_confidence_pct"))
    if valuation_risk is not None and valuation_risk >= 70:
        warnings.append("ABSOLUTE_VALUATION_STRETCHED_SHADOW_ONLY")
    if dilution_pct is not None and dilution_pct >= 50:
        warnings.append("HIGH_NEW_INVESTOR_DILUTION")
    if runway is not None and runway < 1.5:
        warnings.append("POST_IPO_RUNWAY_TIGHT")
    if confidence is not None and confidence < 60:
        warnings.append("OFFER_TERMS_LOW_CONFIDENCE")
    if str(row.get("identity_name_conflict", "")).strip().lower() == "true":
        warnings.append("IDENTITY_CONFLICT")
    return warnings


def _evidence_frame(ranking: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "candidate_id",
        "identity_key",
        "name",
        "symbol",
        "exchange",
        "expected_date",
        "decision",
        "market_readiness_score",
        "opportunity_score",
        "risk_score",
        "net_ipo_score",
        "opportunity_coverage_pct",
        "risk_coverage_pct",
        "sec_cik",
        "sec_form",
        "sec_filing_date",
        "sec_financial_source",
        "sec_ixbrl_status",
        "sec_ixbrl_fact_count",
        "sec_latest_revenue",
        "sec_revenue_growth_pct",
        "sec_latest_gross_margin_pct",
        "sec_cash",
        "sec_latest_operating_cash_flow",
        "sec_cash_runway_years_pre_ipo",
        "sec_ipo_price",
        "sec_primary_shares_offered",
        "sec_secondary_shares_offered",
        "sec_secondary_share_pct",
        "sec_post_offering_shares",
        "sec_primary_gross_proceeds",
        "sec_net_proceeds",
        "sec_pro_forma_cash_before_use_of_proceeds",
        "sec_cash_runway_years_post_ipo_upper_bound",
        "sec_dilution_per_share",
        "sec_dilution_pct",
        "sec_implied_market_cap",
        "sec_ipo_price_to_sales",
        "shadow_absolute_valuation_risk",
        "sec_offer_terms_confidence_pct",
        "opportunity_balance_sheet_post_ipo",
        "risk_dilution_secondary",
        "hard_flags",
        "deep_dd_warnings",
        "sec_prospectus_url",
        "live_order_allowed",
    ]
    if ranking.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict] = []
    for _, series in ranking.iterrows():
        row = series.to_dict()
        record = {column: row.get(column) for column in columns}
        record["deep_dd_warnings"] = "|".join(_warning_flags(row))
        record["live_order_allowed"] = False
        rows.append(record)
    return pd.DataFrame(rows).reindex(columns=columns)


def _deep_brief(evidence: pd.DataFrame) -> dict:
    shortlist: list[dict] = []
    if not evidence.empty:
        selected = evidence.head(12)
        for _, row in selected.iterrows():
            shortlist.append(
                {
                    "name": row.get("name"),
                    "symbol": row.get("symbol"),
                    "expected_date": row.get("expected_date"),
                    "decision": row.get("decision"),
                    "net_ipo_score": row.get("net_ipo_score"),
                    "coverage_min_pct": min(
                        _as_float(row.get("opportunity_coverage_pct")) or 0.0,
                        _as_float(row.get("risk_coverage_pct")) or 0.0,
                    ),
                    "revenue_growth_pct": row.get("sec_revenue_growth_pct"),
                    "gross_margin_pct": row.get("sec_latest_gross_margin_pct"),
                    "ipo_price": row.get("sec_ipo_price"),
                    "implied_market_cap": row.get("sec_implied_market_cap"),
                    "ipo_price_to_sales": row.get("sec_ipo_price_to_sales"),
                    "absolute_valuation_risk_shadow": row.get("shadow_absolute_valuation_risk"),
                    "dilution_pct": row.get("sec_dilution_pct"),
                    "secondary_share_pct": row.get("sec_secondary_share_pct"),
                    "post_ipo_runway_upper_bound_years": row.get("sec_cash_runway_years_post_ipo_upper_bound"),
                    "offer_terms_confidence_pct": row.get("sec_offer_terms_confidence_pct"),
                    "warnings": row.get("deep_dd_warnings"),
                    "hard_flags": row.get("hard_flags"),
                    "prospectus": row.get("sec_prospectus_url"),
                }
            )
    return {
        "module": "IPO_RADAR_V1.2_DEEP_DD",
        "execution_policy": "SHADOW_ADVISORY_ONLY_NO_BUY",
        "shortlist": shortlist,
        "interpretation": {
            "inline_xbrl": "Prospectus Inline XBRL is preferred for pre-IPO financial evidence; SEC Company Facts is fallback only.",
            "valuation": "Absolute price-to-sales risk is diagnostic/shadow and does not populate the peer-relative valuation criterion.",
            "post_ipo_runway": "Runway is an upper bound before planned use of proceeds; a value below one year can conservatively trigger the existing liquidity hard block.",
            "balance_sheet": "Post-IPO balance-sheet score is populated only when prospectus net proceeds are detected.",
        },
        "live_orders_enabled": False,
        "can_create_buy": False,
    }


def _write_deep_outputs(root: Path) -> dict:
    outdir = root / "outputs" / "ipo_radar"
    ranking_path = outdir / "IPO_RANKING.csv"
    evidence_path = outdir / "IPO_DEEP_DD_EVIDENCE.csv"
    brief_path = outdir / "IPO_DEEP_DD_BRIEF.json"
    ranking = pd.read_csv(ranking_path, low_memory=False) if ranking_path.exists() else pd.DataFrame()
    evidence = _evidence_frame(ranking)
    evidence.to_csv(evidence_path, index=False)
    brief = _deep_brief(evidence)
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    deep_count = 0
    if not evidence.empty and "sec_ixbrl_fact_count" in evidence.columns:
        counts = pd.to_numeric(evidence["sec_ixbrl_fact_count"], errors="coerce").fillna(0)
        deep_count = int((counts > 0).sum())
    return {
        "evidence": "outputs/ipo_radar/IPO_DEEP_DD_EVIDENCE.csv",
        "brief": "outputs/ipo_radar/IPO_DEEP_DD_BRIEF.json",
        "candidate_count": int(len(evidence)),
        "inline_xbrl_enriched_count": deep_count,
    }


def run(root: Path = ROOT) -> dict:
    install_v1_2()
    summary = v11.run(root)
    deep_outputs = _write_deep_outputs(root)
    summary["module_version"] = "IPO_RADAR_V1.2"
    summary["deep_dd_layer"] = "INLINE_XBRL_OFFER_TERMS_PRO_FORMA_V1.2"
    summary["financial_evidence_policy"] = "PROSPECTUS_INLINE_XBRL_FIRST_COMPANYFACTS_FALLBACK"
    summary["peer_valuation_policy"] = "DO_NOT_INFER_PEER_RELATIVE_SCORE_FROM_ABSOLUTE_MULTIPLE"
    summary["deep_dd"] = deep_outputs
    summary.setdefault("outputs", {}).update(
        {
            "deep_dd_evidence": deep_outputs["evidence"],
            "deep_dd_brief": deep_outputs["brief"],
        }
    )
    summary_path = root / "outputs" / "ipo_radar" / "IPO_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
