from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from .core import is_observed


CANONICAL_FIELDS = [
    "last_close", "volume", "volume_avg_20d", "high_52w", "low_52w",
    "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "perf_1y_pct",
    "mm20", "mm50", "mm100", "mm200", "rsi14", "stoch_k", "stoch_d",
    "macd_line", "macd_signal", "macd_hist", "atr14", "bollinger_mid",
    "bollinger_upper", "bollinger_lower", "bollinger_width_pct", "sharpe_1y_rf0",
    "volatility_20d", "volatility_60d", "volatility_1y_pct", "max_drawdown_1y", "rvol20",
    "per_forward_v21", "per_ttm_v21", "pb_v21", "roe_v21_pct", "roa_v21_pct",
    "roic_v21_pct", "operating_margin_v21_pct", "net_margin_v21_pct",
    "revenue_growth_v21_pct", "revenue_cagr_5y_v21_pct", "earnings_growth_v21_pct",
    "ev_to_ebitda_v21", "debt_to_equity_v21", "debt_to_ebitda_v21",
    "current_ratio_v21", "interest_coverage_v21", "dividend_yield_v21_pct",
    "beta_v21", "fcf_yield_v21", "target_mean_v21", "target_low_v21",
    "target_high_v21", "target_median_v21", "n_analysts_v21", "consensus_score_100_v21",
    "consensus_score_100_4w_ago_v21", "consensus_delta_4w", "upgrades_30d_v21",
    "downgrades_30d_v21", "net_upgrades_30d_v21", "broker_weighted_revision_30d",
]

SUPPLEMENTAL_FIELDS = [
    "euronext_live_last_price", "euronext_live_bid", "euronext_live_ask",
    "free_float_pct", "spread_pct",
    "amf_public_net_short_pct", "amf_public_net_short_max_holder_pct",
    "amf_public_net_short_holders", "amf_public_net_short_latest_date",
]


def _missing(series: pd.Series) -> pd.Series:
    return ~series.map(is_observed)


def _context_parts(context: dict) -> tuple[dict, dict, dict, dict, dict, dict, dict]:
    fred = context.get("fred", {}) if isinstance(context.get("fred"), dict) else {}
    eia = context.get("eia", {}) if isinstance(context.get("eia"), dict) else {}
    ecb = context.get("ecb", {}) if isinstance(context.get("ecb"), dict) else {}
    sentiment = context.get("market_sentiment", {}) if isinstance(context.get("market_sentiment"), dict) else {}
    news = context.get("global_news", {}) if isinstance(context.get("global_news"), dict) else {}
    fear = sentiment.get("fear_greed", {}) if isinstance(sentiment.get("fear_greed"), dict) else {}
    aaii = sentiment.get("aaii", {}) if isinstance(sentiment.get("aaii"), dict) else {}
    return fred, eia, ecb, sentiment, news, fear, aaii


def _context_values(context: dict) -> dict[str, object]:
    fred, eia, ecb, sentiment, news, fear, aaii = _context_parts(context)
    return {
        "v211_macro_vix": fred.get("macro_vix"),
        "v211_macro_curve_10y2y": fred.get("macro_curve_10y2y"),
        "v211_macro_cpi_index": fred.get("macro_cpi_index"),
        "v211_macro_inflation_yoy_pct": fred.get("macro_inflation_yoy_pct"),
        "v211_macro_pmi": fred.get("macro_pmi"),
        "v211_macro_pmi_status": fred.get("macro_pmi_status"),
        "v211_macro_as_of": fred.get("macro_as_of"),
        "v211_ecb_deposit_rate_pct": ecb.get("deposit_rate_pct"),
        "v211_ecb_recent_change_pp": ecb.get("recent_change_pp"),
        "v211_ecb_direction_score": ecb.get("direction_score"),
        "v211_wti_spot_usd_bbl": eia.get("wti_spot_usd_bbl"),
        "v211_brent_spot_usd_bbl": eia.get("brent_spot_usd_bbl"),
        "v211_brent_wti_spread_usd_bbl": eia.get("brent_wti_spread_usd_bbl"),
        "v211_energy_as_of": eia.get("energy_as_of"),
        "v211_fear_greed_index": fear.get("score"),
        "v211_fear_greed_rating": fear.get("rating"),
        "v211_fear_greed_asof": fear.get("asof"),
        "v211_aaii_bullish_pct": aaii.get("bullish_pct"),
        "v211_aaii_neutral_pct": aaii.get("neutral_pct"),
        "v211_aaii_bearish_pct": aaii.get("bearish_pct"),
        "v211_aaii_bull_bear_spread": aaii.get("bull_bear_spread"),
        "v211_aaii_asof": aaii.get("asof"),
        "v211_sentiment_status": sentiment.get("status"),
        "v211_global_news_score": news.get("score"),
        "v211_global_news_polarity": news.get("polarity"),
        "v211_global_news_materiality_score": news.get("materiality_score"),
        "v211_global_news_material_articles": news.get("material_articles"),
        "v211_global_news_articles": news.get("articles"),
        "v211_global_news_source_mode": news.get("source_mode"),
        "v211_context_generated_at_utc": context.get("generated_at_utc"),
    }


def _fill_constant_missing(df: pd.DataFrame, field: str, value: object) -> int:
    if not is_observed(value):
        return 0
    if field not in df.columns:
        df[field] = pd.NA
    mask = _missing(df[field])
    if mask.any():
        df.loc[mask, field] = value
    return int(mask.sum())


def _apply_canonical_context_fallbacks(df: pd.DataFrame, context: dict) -> dict[str, int]:
    """Populate existing funnel fields only when absent; no scoring rule or weight is changed."""
    _, _, _, sentiment, news, fear, aaii = _context_parts(context)
    mapping = {
        "fear_greed_index": fear.get("score"),
        "fear_greed_rating": fear.get("rating"),
        "fear_greed_asof": fear.get("asof"),
        "fear_greed_source": fear.get("source"),
        "aaii_bullish_pct": aaii.get("bullish_pct"),
        "aaii_neutral_pct": aaii.get("neutral_pct"),
        "aaii_bearish_pct": aaii.get("bearish_pct"),
        "aaii_bull_bear_spread": aaii.get("bull_bear_spread"),
        "aaii_asof": aaii.get("asof"),
        "aaii_source": aaii.get("source"),
        "sentiment_data_status": sentiment.get("status"),
        "sentiment_collected_at_utc": sentiment.get("collected_at_utc"),
        "global_news_score_v211_fallback": news.get("score"),
        "global_news_polarity_v211": news.get("polarity"),
        "global_news_materiality_v211": news.get("materiality_score"),
    }
    return {field: count for field, value in mapping.items() if (count := _fill_constant_missing(df, field, value)) > 0}


def _apply_from_merged(df: pd.DataFrame, src: pd.DataFrame, audit: dict) -> None:
    if "isin" not in src.columns or src["isin"].astype(str).duplicated().any():
        return
    src = src.copy()
    src["isin"] = src["isin"].astype(str)
    df["isin"] = df["isin"].astype(str)
    indexed = src.set_index("isin")
    target_idx = df["isin"]
    audit["source_rows_available"] = len(src)
    for field in CANONICAL_FIELDS + SUPPLEMENTAL_FIELDS:
        source_tag = f"v211_{field}_source"
        if field not in src.columns or source_tag not in src.columns:
            continue
        if field not in df.columns:
            df[field] = pd.NA
        source_values = target_idx.map(indexed[field].to_dict())
        source_tags = target_idx.map(indexed[source_tag].to_dict())
        eligible = source_tags.map(is_observed)
        missing = _missing(df[field])
        apply_mask = eligible & missing & source_values.map(is_observed)
        if not apply_mask.any():
            continue
        before = df.loc[apply_mask, field].copy()
        df.loc[apply_mask, field] = source_values[apply_mask]
        if before.map(is_observed).any():
            audit["overwrites"] += int(before.map(is_observed).sum())
        count = int(apply_mask.sum())
        audit["free_cells_applied"] += count
        audit["free_fields_applied"][field] = int(audit["free_fields_applied"].get(field, 0)) + count
        if field in SUPPLEMENTAL_FIELDS:
            audit["supplemental_cells_applied"] += count
        for suffix in ("source", "as_of", "confidence", "freshness"):
            col = f"v211_{field}_{suffix}"
            if col in src.columns:
                if col not in df.columns:
                    df[col] = pd.NA
                values = target_idx.map(indexed[col].to_dict())
                df.loc[apply_mask, col] = values[apply_mask]


def _apply_overlay_supplemental(df: pd.DataFrame, overlay: pd.DataFrame, audit: dict) -> None:
    if "isin" not in overlay.columns or overlay["isin"].astype(str).duplicated().any():
        return
    overlay = overlay.copy()
    overlay["isin"] = overlay["isin"].astype(str)
    idx = overlay.set_index("isin")
    target_idx = df["isin"].astype(str)

    for col in [c for c in overlay.columns if c.startswith("free_identity_")]:
        values = target_idx.map(idx[col].to_dict())
        if col not in df.columns:
            df[col] = values
            audit["identity_fields_added"] += 1
        else:
            mask = _missing(df[col]) & values.map(is_observed)
            df.loc[mask, col] = values[mask]

    # Overlay contains every captured fact, including fields not yet present in a canonical
    # reference. Carry approved supplemental fields so AMF/Euronext/Finnhub data cannot be
    # silently lost merely because an older reference schema lacked a column.
    for field in SUPPLEMENTAL_FIELDS + [
        "roic_v21_pct", "revenue_cagr_5y_v21_pct", "ev_to_ebitda_v21",
        "upgrades_30d_v21", "downgrades_30d_v21", "net_upgrades_30d_v21",
        "broker_weighted_revision_30d", "mm100", "macd_line", "macd_signal", "macd_hist",
        "atr14", "bollinger_mid", "bollinger_upper", "bollinger_lower", "bollinger_width_pct",
        "sharpe_1y_rf0", "stoch_k", "stoch_d", "volatility_1y_pct",
    ]:
        value_col = f"free_{field}"
        source_col = f"free_{field}_source"
        asof_col = f"free_{field}_as_of"
        if value_col not in overlay.columns:
            continue
        if field not in df.columns:
            df[field] = pd.NA
        values = target_idx.map(idx[value_col].to_dict())
        sources = target_idx.map(idx[source_col].to_dict()) if source_col in overlay.columns else pd.Series(pd.NA, index=df.index)
        mask = _missing(df[field]) & values.map(is_observed)
        if source_col in overlay.columns:
            mask &= sources.map(is_observed)
        if not mask.any():
            continue
        before = df.loc[mask, field].copy()
        df.loc[mask, field] = values[mask]
        if before.map(is_observed).any():
            audit["overwrites"] += int(before.map(is_observed).sum())
        count = int(mask.sum())
        audit["free_cells_applied"] += count
        audit["supplemental_cells_applied"] += count
        audit["free_fields_applied"][field] = int(audit["free_fields_applied"].get(field, 0)) + count
        provenance = f"v211_{field}_source"
        if provenance not in df.columns:
            df[provenance] = pd.NA
        df.loc[mask, provenance] = sources[mask]
        if asof_col in overlay.columns:
            asofs = target_idx.map(idx[asof_col].to_dict())
            out_col = f"v211_{field}_as_of"
            if out_col not in df.columns:
                df[out_col] = pd.NA
            df.loc[mask, out_col] = asofs[mask]


def apply(target: Path, free_root: Path, expected_rows: int | None = None) -> dict:
    df = pd.read_csv(target, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if "isin" not in df.columns or df["isin"].astype(str).duplicated().any():
        raise RuntimeError("Free propagation target requires unique ISIN")
    if expected_rows is not None and len(df) != int(expected_rows):
        raise RuntimeError(f"Free propagation target row gate failed: {len(df)} != {expected_rows}")

    audit = {
        "passed": True,
        "target": str(target),
        "rows": len(df),
        "free_cells_applied": 0,
        "supplemental_cells_applied": 0,
        "free_fields_applied": {},
        "identity_fields_added": 0,
        "context_fields_added": 0,
        "canonical_context_fallback_cells": 0,
        "canonical_context_fallback_fields": {},
        "overwrites": 0,
        "source_rows_available": 0,
        "policy": "MISSING_ONLY_ZERO_OVERWRITE_WITH_PROVENANCE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    merged_path = free_root / "V21.1_ACTIONS_PEA_REFERENCE_MERGED.csv"
    overlay_path = free_root / "V21.1_FREE_CAPTURE_OVERLAY.csv"
    if merged_path.exists():
        src = pd.read_csv(merged_path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
        _apply_from_merged(df, src, audit)

    if overlay_path.exists():
        overlay = pd.read_csv(overlay_path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
        _apply_overlay_supplemental(df, overlay, audit)

    context_path = free_root / "V21.1_COMPLEMENTARY_CONTEXT.json"
    if context_path.exists():
        context = json.loads(context_path.read_text(encoding="utf-8"))
        for field, value in _context_values(context).items():
            count = _fill_constant_missing(df, field, value)
            if count:
                audit["context_fields_added"] += 1
        fallback = _apply_canonical_context_fallbacks(df, context)
        audit["canonical_context_fallback_fields"] = fallback
        audit["canonical_context_fallback_cells"] = int(sum(fallback.values()))
        hicp = context.get("eurostat_hicp", {}) if isinstance(context.get("eurostat_hicp"), dict) else {}
        for country, payload in sorted(hicp.items()):
            if not isinstance(payload, dict):
                continue
            for suffix, source_field in (("yoy_pct", "hicp_yoy_pct"), ("inflation_score", "inflation_score"), ("period", "period")):
                value = payload.get(source_field)
                field = f"v211_hicp_{country.lower()}_{suffix}"
                count = _fill_constant_missing(df, field, value)
                if count:
                    audit["context_fields_added"] += 1

    sector = df.get("sector_v21", df.get("sector_yf", df.get("category", pd.Series("", index=df.index))))
    sector = sector.astype(str).str.lower()
    df["v211_energy_context_applicable"] = sector.str.contains("energy|oil|gas|petroleum|énergie", regex=True, na=False)
    df["v211_free_capture_propagated"] = True
    df["v211_free_capture_propagated_at_utc"] = datetime.now(timezone.utc).isoformat()

    audit["passed"] = audit["overwrites"] == 0
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(target, sep=";", index=False, encoding="utf-8-sig")
    audit_path = target.parent / "audit" / f"V21.1_PROPAGATION_{target.stem}.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--free-root", default="outputs/free_capture")
    parser.add_argument("--expected-rows", type=int, default=None)
    args = parser.parse_args()
    audit = apply(Path(args.target), Path(args.free_root), args.expected_rows)
    if not audit["passed"]:
        raise RuntimeError(f"Free capture propagation gate failed: {audit}")
    print("V21_1_FREE_PROPAGATION_OK", json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
