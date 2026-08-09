from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
IN = ROOT / "outputs/V20.4.3_ETF102_COMMITTEE.csv"
MASTER = ROOT / "outputs/V20.5_ETF102_REFERENCE_MASTER.csv"
COMPLETE = ROOT / "outputs/V20.5_ETF102_REFERENCE_COMPLETE.csv"
COVERAGE = ROOT / "outputs/V20.5_ETF102_REFERENCE_COVERAGE.csv"
GAPS = ROOT / "outputs/V20.5_ETF102_REFERENCE_GAPS.csv"
AUDIT = ROOT / "outputs/audit/V20.5_ETF102_REFERENCE_AUDIT.json"

VERSION = "V20.5_ETF102_REFERENCE_MASTER"
MISSING_POLICY = "VALUE_OR_EXPLICIT_NA_NO_NEUTRAL_50"

MANDATORY_FIELDS = [
    "isin", "name", "asset_class", "pea_type", "pea_confidence", "provider", "category",
    "geo_exposure", "ticker_primary", "primary_exchange", "primary_mic", "trading_currency",
    "ticker_identity_status", "ticker_confidence_pct", "euronext_symbol", "yahoo_ticker",
    "referential_status", "funnel_global_macro_score", "funnel_context_score",
    "funnel_context_coverage", "funnel_macro_multiplier", "funnel_risk_gate",
    "ifs_effective", "smart_money_confidence", "smart_money_data_status",
    "score_ct", "score_mt", "score_lt", "score_short", "rank_ct", "rank_mt", "rank_lt", "rank_short",
    "decision_ct", "decision_mt", "decision_lt", "decision_short", "execution", "legacy_266_used",
]

HIGH_VALUE_OPTIONAL_FIELDS = [
    "dividend_yield_pct", "morningstar_rating", "perf_1y_pct", "perf_3y_pct", "perf_5y_pct",
    "rank_cat_1y", "rank_cat_3y", "rank_cat_5y", "ter_pct", "aum_m", "holdings",
    "volatility_1y_pct", "max_drawdown_1y_pct", "official_benchmark", "distribution_frequency",
    "fund_total_assets_eur_m", "risk_indicator", "tracking_error_1y_pct", "tracking_error_3y_pct",
    "tracking_error_5y_pct", "official_exchange", "spread_pct", "diversification_direct_score",
    "funnel_country_macro_score", "funnel_sector_news_score", "funnel_instrument_news_score",
]

IDENTITY_FIELDS = {
    "isin", "name", "asset_class", "pea_type", "pea_confidence", "provider", "country_domicile",
    "region_domicile", "category", "morningstar_category", "geo_exposure", "style_factor",
    "distribution_policy", "replication_hint", "ticker_euronext", "ticker_yahoo", "mic", "currency",
    "mapping_status", "original_isin", "isin_checksum_before", "isin_correction_status",
    "isin_validation_source", "isin_validation_as_of", "official_exchange", "isin_checksum_after",
    "ticker_validation_as_of", "ticker_validation_wave", "ticker_primary", "primary_exchange",
    "primary_mic", "trading_currency", "ticker_identity_status", "ticker_confidence_pct",
    "ticker_source_url_final", "ticker_source_type", "ticker_validated_as_of", "ticker_yahoo_final",
    "ticker_yahoo_status", "referential_status", "referential_actualised_as_of", "yahoo_ticker",
    "euronext_symbol", "euronext_mic",
}

STRUCTURE_FIELDS = {
    "dividend_yield_pct", "dividend_data_status", "morningstar_rating", "ter_pct", "aum_m", "holdings",
    "official_benchmark", "distribution_frequency", "fund_total_assets_eur_m", "risk_indicator",
    "tracking_error_1y_pct", "tracking_error_3y_pct", "tracking_error_5y_pct", "direct_total_assets_raw",
    "direct_ter_pct", "direct_morningstar_rating", "direct_spread_pct", "direct_beta3y", "direct_nav",
    "direct_diversification_score", "direct_top_holdings_concentration_pct", "direct_sector_hhi",
    "direct_source", "direct_source_url", "direct_collected_at_utc", "direct_fx_to_eur", "direct_aum_eur_m",
    "direct_dividend_yield_pct", "direct_holdings_count", "direct_benchmark", "direct_distribution_frequency",
    "spread_pct", "diversification_direct_score", "rank_cat_method", "rank_cat_peer_group",
}

PERFORMANCE_FIELDS = {
    "perf_1y_pct", "perf_3y_pct", "perf_5y_pct", "rank_cat_1y", "rank_cat_3y", "rank_cat_5y",
    "perf_data_status", "perf_as_of", "mm20", "mm50", "mm100", "mm200", "rsi14", "macd",
    "macd_signal", "macd_hist", "atr14", "bb_mid", "bb_upper", "bb_lower", "rvol20",
    "volatility_20d", "volatility_60d", "volatility_1y_pct", "max_drawdown_1y", "max_drawdown_1y_pct",
    "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "positive_reversal_flag", "last_close", "volume",
    "relative_strength", "direct_history_asof", "direct_perf_1y_pct", "direct_perf_3y_pct",
    "direct_perf_5y_pct", "direct_volatility_1y_pct", "direct_max_drawdown_1y_pct",
}

SENTIMENT_FIELDS = {
    "fear_greed_index", "fear_greed_rating", "fear_greed_asof", "fear_greed_source", "aaii_bullish_pct",
    "aaii_neutral_pct", "aaii_bearish_pct", "aaii_bull_bear_spread", "aaii_asof", "aaii_source",
    "sentiment_data_status", "sentiment_collected_at_utc",
}

FUNNEL_FIELDS = {
    "funnel_country_code", "funnel_global_macro_score", "funnel_country_macro_score",
    "funnel_global_news_score", "funnel_country_news_score", "funnel_sector_news_score",
    "funnel_instrument_news_score", "funnel_market_sentiment_score", "funnel_context_score",
    "funnel_context_coverage", "funnel_macro_multiplier", "funnel_risk_gate",
}

SMART_MONEY_FIELDS = {
    "ifs_raw", "ifs_effective", "smart_money_confidence", "institutional_flow_label", "flow_status",
    "flow_history_snapshots", "flow_observations", "smart_money_data_status", "smart_money_source_completeness",
}


def _nonempty(series: pd.Series) -> pd.Series:
    s = series.astype("string")
    return s.notna() & s.str.strip().fillna("").ne("") & ~s.str.lower().isin(["nan", "none", "null", "<na>"])


def _group(field: str) -> str:
    if field in IDENTITY_FIELDS:
        return "IDENTITY"
    if field in STRUCTURE_FIELDS:
        return "ETF_STRUCTURE"
    if field in PERFORMANCE_FIELDS:
        return "MARKET_TECHNICAL"
    if field in SENTIMENT_FIELDS:
        return "SENTIMENT"
    if field in FUNNEL_FIELDS or field.startswith("macro_multiplier_"):
        return "FUNNEL_MACRO_NEWS"
    if field in SMART_MONEY_FIELDS or field.startswith("smart_money_gate_"):
        return "SMART_MONEY"
    if field.startswith("component_") or field.startswith("effective_weight_") or field.startswith("contrib_"):
        return "SCORING_COMPONENTS"
    if field.startswith("score_") or field.startswith("rank_") or field.startswith("decision_") or field.startswith("selection_"):
        return "DECISION"
    if field in {"execution", "v2043_version", "legacy_266_used", "etf102_bonus_malus"}:
        return "GOVERNANCE"
    if field.startswith("direct_"):
        return "ETF_STRUCTURE"
    return "PROVENANCE_OTHER"


def _source_semantics(field: str, group: str) -> str:
    if group == "IDENTITY":
        return "Validated ETF102 reference / Euronext identity chain"
    if group == "ETF_STRUCTURE":
        if field.startswith("direct_"):
            return "Yahoo Finance fund metadata, observed only"
        if field.startswith("rank_cat_"):
            return "Official value preserved; otherwise ETF102 peer-group derived rank"
        return "Validated base metadata + direct fund metadata backfill"
    if group == "MARKET_TECHNICAL":
        return "Validated Yahoo market history / technical engine; 1y/3y/5y gaps backfilled from 5y price history"
    if group == "SENTIMENT":
        return "CNN Fear & Greed + AAII"
    if group == "FUNNEL_MACRO_NEWS":
        return "FRED + ECB + Eurostat HICP + GDELT/Google News RSS + sentiment"
    if group == "SMART_MONEY":
        return "V18.3 persistent Smart Money shadow; no positive score boost before empirical walk-forward"
    if group == "SCORING_COMPONENTS":
        return "V20.5 availability-aware scoring; weights renormalized on observed components"
    if group == "DECISION":
        return "V20.5 CT/MT/LT/SHORT committee engine"
    if group == "GOVERNANCE":
        return "Research-only governance; legacy 266 forbidden"
    return "Inherited validated reference/provenance"


def _coverage_status(pct: float) -> str:
    if pct >= 100.0 - 1e-9:
        return "FULL"
    if pct >= 80.0:
        return "HIGH"
    if pct >= 50.0:
        return "PARTIAL"
    if pct > 0:
        return "LOW"
    return "UNAVAILABLE"


def _safe_backfills(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    def text_fill(target: str, source: str) -> None:
        if target not in out.columns or source not in out.columns:
            return
        old = _nonempty(out[target])
        new = _nonempty(out[source])
        out.loc[~old & new, target] = out.loc[~old & new, source]

    def numeric_fill(target: str, source: str) -> None:
        if target not in out.columns or source not in out.columns:
            return
        old = pd.to_numeric(out[target], errors="coerce")
        new = pd.to_numeric(out[source], errors="coerce")
        out[target] = old.where(old.notna(), new)

    for target, source in [
        ("ticker_euronext", "euronext_symbol"),
        ("ticker_yahoo", "yahoo_ticker"),
        ("official_exchange", "primary_exchange"),
        ("ticker_validation_as_of", "ticker_validated_as_of"),
        ("source_name", "direct_source"),
        ("source_url", "direct_source_url"),
    ]:
        text_fill(target, source)

    for target, source in [
        ("aum_m", "fund_total_assets_eur_m"),
        ("max_drawdown_1y_pct", "max_drawdown_1y"),
    ]:
        numeric_fill(target, source)

    return out


def build(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    if len(df) != 102 or df["isin"].astype(str).nunique() != 102:
        raise RuntimeError("V20.5 ETF102 reference requires exactly 102 unique ISIN")
    if "legacy_266_used" not in df.columns or not df["legacy_266_used"].astype(str).str.lower().eq("false").all():
        raise RuntimeError("Legacy 266 contamination gate failed")

    original_columns = list(df.columns)
    out = _safe_backfills(df)
    generated = datetime.now(timezone.utc).isoformat()
    today = generated[:10]
    if "referential_actualised_as_of" in out.columns:
        out["referential_actualised_as_of"] = today

    # Coverage is measured before adding reference bookkeeping columns.
    coverage_rows = []
    for field in original_columns:
        populated = int(_nonempty(out[field]).sum())
        missing = len(out) - populated
        pct = round(populated / len(out) * 100.0, 2)
        group = _group(field)
        coverage_rows.append({
            "field": field,
            "group": group,
            "populated": populated,
            "missing": missing,
            "coverage_pct": pct,
            "coverage_status": _coverage_status(pct),
            "mandatory": field in MANDATORY_FIELDS,
            "high_value_optional": field in HIGH_VALUE_OPTIONAL_FIELDS,
            "source_semantics": _source_semantics(field, group),
        })
    coverage = pd.DataFrame(coverage_rows)

    missing_mandatory = []
    for field in MANDATORY_FIELDS:
        if field not in out.columns:
            missing_mandatory.append(field)
        elif int(_nonempty(out[field]).sum()) != 102:
            missing_mandatory.append(field)
    if missing_mandatory:
        raise RuntimeError(f"Mandatory ETF102 reference fields incomplete: {missing_mandatory}")

    # Row-level completeness and gap transparency.
    nonempty_matrix = pd.DataFrame({c: _nonempty(out[c]) for c in original_columns})
    row_counts = nonempty_matrix.sum(axis=1)
    out["reference_version"] = VERSION
    out["reference_generated_at_utc"] = generated
    out["reference_missing_data_policy"] = MISSING_POLICY
    out["reference_populated_fields"] = row_counts.astype(int)
    out["reference_total_fields"] = len(original_columns)
    out["reference_completeness_pct"] = (row_counts / len(original_columns) * 100.0).round(2)

    high_value_missing: list[str] = []
    row_status: list[str] = []
    for idx in out.index:
        missing = [f for f in HIGH_VALUE_OPTIONAL_FIELDS if f in out.columns and not bool(_nonempty(out.loc[[idx], f]).iloc[0])]
        high_value_missing.append("|".join(missing) if missing else "NONE")
        comp = float(out.loc[idx, "reference_completeness_pct"])
        row_status.append("COMPLETE_HIGH" if comp >= 90.0 else ("COMPLETE_PARTIAL" if comp >= 75.0 else "DATA_GAPS_REMAIN"))
    out["reference_high_value_missing_fields"] = high_value_missing
    out["reference_status"] = row_status
    out["reference_update_note"] = "ETF102_ONLY; V20.5 funnel + Smart Money shadow + scoring consolidated; no legacy 266"

    gap_rows = []
    for _, row in coverage[coverage["missing"] > 0].sort_values(["mandatory", "high_value_optional", "coverage_pct"], ascending=[False, False, True]).iterrows():
        field = str(row["field"])
        missing_isins = out.loc[~_nonempty(out[field]), "isin"].astype(str).tolist()
        gap_rows.append({
            "field": field,
            "group": row["group"],
            "coverage_pct": row["coverage_pct"],
            "missing_count": row["missing"],
            "missing_isins": "|".join(missing_isins),
            "status": row["coverage_status"],
            "action": "SOURCE_REQUIRED" if float(row["coverage_pct"]) == 0 else "BACKFILL_WHEN_SOURCE_AVAILABLE",
        })
    gaps = pd.DataFrame(gap_rows)

    group_summary = (
        coverage.groupby("group", as_index=False)
        .agg(fields=("field", "count"), mean_coverage_pct=("coverage_pct", "mean"), full_fields=("coverage_status", lambda s: int((s == "FULL").sum())))
    )
    group_summary["mean_coverage_pct"] = group_summary["mean_coverage_pct"].round(2)

    audit = {
        "passed": True,
        "version": VERSION,
        "generated_at_utc": generated,
        "rows": len(out),
        "unique_isin": int(out["isin"].astype(str).nunique()),
        "legacy_266_used": False,
        "original_committee_columns": len(original_columns),
        "reference_columns": len(out.columns),
        "mandatory_fields": len(MANDATORY_FIELDS),
        "mandatory_fields_full": len(MANDATORY_FIELDS),
        "missing_data_policy": MISSING_POLICY,
        "presentation_missing_cells_replaced_with": "N/A",
        "mean_field_coverage_pct": round(float(coverage["coverage_pct"].mean()), 2),
        "full_coverage_fields": int((coverage["coverage_status"] == "FULL").sum()),
        "unavailable_fields": coverage.loc[coverage["coverage_status"] == "UNAVAILABLE", "field"].astype(str).tolist(),
        "high_value_optional_gaps": coverage.loc[(coverage["high_value_optional"] == True) & (coverage["coverage_pct"] < 100), ["field", "populated", "missing", "coverage_pct"]].to_dict("records"),
        "group_coverage": group_summary.to_dict("records"),
        "row_completeness": {
            "min_pct": round(float(pd.to_numeric(out["reference_completeness_pct"], errors="coerce").min()), 2),
            "mean_pct": round(float(pd.to_numeric(out["reference_completeness_pct"], errors="coerce").mean()), 2),
            "max_pct": round(float(pd.to_numeric(out["reference_completeness_pct"], errors="coerce").max()), 2),
        },
        "contracts": {
            "universe": "ETF102_ONLY",
            "identity": "FINAL_VALIDATED",
            "scoring": "RENORMALIZE_OBSERVED_WEIGHTS_NO_NEUTRAL_50",
            "smart_money": "NEGATIVE_HIGH_CONFIDENCE_GATE_ONLY_UNTIL_20_DISTINCT_WALK_FORWARD_DATES",
            "execution": "RESEARCH_ONLY",
        },
    }
    return out, coverage, gaps, audit


def main() -> None:
    if not IN.exists():
        raise RuntimeError(f"Missing final ETF102 committee file: {IN}")
    df = pd.read_csv(IN, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    out, coverage, gaps, audit = build(df)

    MASTER.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(MASTER, sep=";", index=False, encoding="utf-8-sig")

    # Presentation/reference-view contract: no silent blank cell. Numerical N/A
    # remains N/A only in this display file; the machine master keeps true nulls.
    display = out.copy().replace({np.nan: pd.NA})
    display = display.fillna("N/A")
    for col in display.columns:
        s = display[col].astype("string")
        display[col] = s.where(s.str.strip().fillna("").ne(""), "N/A")
    if bool((display.astype("string").apply(lambda s: s.str.strip().eq("")).any()).any()):
        raise RuntimeError("Complete reference presentation still contains blank cells")
    display.to_csv(COMPLETE, sep=";", index=False, encoding="utf-8-sig")
    coverage.to_csv(COVERAGE, sep=";", index=False, encoding="utf-8-sig")
    gaps.to_csv(GAPS, sep=";", index=False, encoding="utf-8-sig")
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    print("V20.5_ETF102_REFERENCE_COMPLETE_OK", json.dumps({
        "rows": len(out),
        "columns": len(out.columns),
        "mean_field_coverage_pct": audit["mean_field_coverage_pct"],
        "full_fields": audit["full_coverage_fields"],
        "unavailable_fields": audit["unavailable_fields"],
        "row_completeness": audit["row_completeness"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
