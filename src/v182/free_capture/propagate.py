from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from .core import is_observed


CANONICAL_FIELDS = [
    "last_close", "volume", "high_52w", "low_52w", "perf_1m_pct", "perf_3m_pct",
    "perf_6m_pct", "perf_1y_pct", "mm20", "mm50", "mm200", "rsi14",
    "volatility_20d", "volatility_60d", "max_drawdown_1y", "rvol20",
    "per_forward_v21", "pb_v21", "roe_v21_pct", "roa_v21_pct",
    "operating_margin_v21_pct", "net_margin_v21_pct", "revenue_growth_v21_pct",
    "earnings_growth_v21_pct", "debt_to_equity_v21", "debt_to_ebitda_v21",
    "current_ratio_v21", "interest_coverage_v21", "dividend_yield_v21_pct",
    "beta_v21", "fcf_yield_v21", "target_mean_v21", "target_low_v21",
    "target_high_v21", "n_analysts_v21", "consensus_score_100_v21",
]


def _missing(series: pd.Series) -> pd.Series:
    return ~series.map(is_observed)


def _context_values(context: dict) -> dict[str, object]:
    fred = context.get("fred", {}) if isinstance(context.get("fred"), dict) else {}
    eia = context.get("eia", {}) if isinstance(context.get("eia"), dict) else {}
    ecb = context.get("ecb", {}) if isinstance(context.get("ecb"), dict) else {}
    sentiment = context.get("market_sentiment", {}) if isinstance(context.get("market_sentiment"), dict) else {}
    news = context.get("global_news", {}) if isinstance(context.get("global_news"), dict) else {}
    fear = sentiment.get("fear_greed", {}) if isinstance(sentiment.get("fear_greed"), dict) else {}
    aaii = sentiment.get("aaii", {}) if isinstance(sentiment.get("aaii"), dict) else {}
    return {
        "v211_macro_vix": fred.get("macro_vix"),
        "v211_macro_curve_10y2y": fred.get("macro_curve_10y2y"),
        "v211_macro_as_of": fred.get("macro_as_of"),
        "v211_ecb_deposit_rate_pct": ecb.get("deposit_rate_pct"),
        "v211_ecb_recent_change_pp": ecb.get("recent_change_pp"),
        "v211_wti_spot_usd_bbl": eia.get("wti_spot_usd_bbl"),
        "v211_brent_spot_usd_bbl": eia.get("brent_spot_usd_bbl"),
        "v211_brent_wti_spread_usd_bbl": eia.get("brent_wti_spread_usd_bbl"),
        "v211_energy_as_of": eia.get("energy_as_of"),
        "v211_fear_greed_index": fear.get("score"),
        "v211_fear_greed_rating": fear.get("rating"),
        "v211_aaii_bull_bear_spread": aaii.get("bull_bear_spread"),
        "v211_global_news_score": news.get("score"),
        "v211_global_news_articles": news.get("articles"),
        "v211_context_generated_at_utc": context.get("generated_at_utc"),
    }


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
        "free_fields_applied": {},
        "identity_fields_added": 0,
        "context_fields_added": 0,
        "overwrites": 0,
        "source_rows_available": 0,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    merged_path = free_root / "V21.1_ACTIONS_PEA_REFERENCE_MERGED.csv"
    overlay_path = free_root / "V21.1_FREE_CAPTURE_OVERLAY.csv"
    if merged_path.exists():
        src = pd.read_csv(merged_path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
        if "isin" in src.columns and not src["isin"].astype(str).duplicated().any():
            src["isin"] = src["isin"].astype(str)
            df["isin"] = df["isin"].astype(str)
            indexed = src.set_index("isin")
            audit["source_rows_available"] = len(src)
            target_idx = df["isin"]
            for field in CANONICAL_FIELDS:
                source_tag = f"v211_{field}_source"
                if field not in df.columns or field not in src.columns or source_tag not in src.columns:
                    continue
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
                audit["free_fields_applied"][field] = count
                for suffix in ("source", "as_of", "confidence", "freshness"):
                    col = f"v211_{field}_{suffix}"
                    if col in src.columns:
                        if col not in df.columns:
                            df[col] = pd.NA
                        values = target_idx.map(indexed[col].to_dict())
                        df.loc[apply_mask, col] = values[apply_mask]

    if overlay_path.exists():
        overlay = pd.read_csv(overlay_path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
        if "isin" in overlay.columns and not overlay["isin"].astype(str).duplicated().any():
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

    context_path = free_root / "V21.1_COMPLEMENTARY_CONTEXT.json"
    if context_path.exists():
        context = json.loads(context_path.read_text(encoding="utf-8"))
        for field, value in _context_values(context).items():
            if is_observed(value):
                df[field] = value
                audit["context_fields_added"] += 1
        hicp = context.get("eurostat_hicp", {}) if isinstance(context.get("eurostat_hicp"), dict) else {}
        for country, payload in sorted(hicp.items()):
            if not isinstance(payload, dict):
                continue
            value = payload.get("hicp_yoy_pct")
            if is_observed(value):
                df[f"v211_hicp_{country.lower()}_yoy_pct"] = value
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
