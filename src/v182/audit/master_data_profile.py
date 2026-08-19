from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from v182.audit.canonical_universe import filter_actions
from v182.io.frames import is_missing, load_master

FIELD_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "ACTION": {
        "identity": ("isin", "name", "yahoo_ticker", "exchange", "country", "currency"),
        "qualitative": (
            "sector", "industry", "country", "exchange", "currency", "pea_confidence",
            "corporate_status", "broker_pea_confirmed", "source", "evidence_level",
        ),
        "quantitative_fundamental": (
            "market_cap", "per_ttm_yf", "per_forward_yf", "pb", "roe_api", "roa",
            "debt_to_equity", "free_cash_flow", "marge_ebit", "marge_nette", "beta",
            "dividend_yield_pct", "payout_ratio", "target_price", "n_analysts",
        ),
        "quantitative_market": (
            "last_close", "volume", "perf_10d_pct", "perf_1m_pct", "perf_3m_pct",
            "perf_6m_pct", "perf_1y_pct", "volatility_20d", "volatility_60d",
            "volatility_1y_pct", "max_drawdown_1y", "high_52w", "distance_high_52w_pct",
            "rsi14", "macd", "atr14_pct", "rvol20",
        ),
    },
    "ETF": {
        "identity": (
            "isin", "name", "provider", "yahoo_ticker", "ticker_primary", "primary_exchange",
            "primary_mic", "trading_currency",
        ),
        "qualitative": (
            "pea_type", "pea_confidence", "provider", "country_domicile", "region_domicile",
            "category", "morningstar_category", "geo_exposure", "style_factor",
            "distribution_policy", "replication_hint", "official_benchmark", "official_exchange",
            "referential_status", "ticker_identity_status",
        ),
        "quantitative_fundamental": (
            "ter_pct", "aum_m", "fund_total_assets_eur_m", "holdings", "dividend_yield_pct",
            "morningstar_rating", "risk_indicator", "tracking_error_1y_pct",
            "tracking_error_3y_pct", "tracking_error_5y_pct",
        ),
        "quantitative_market": (
            "perf_1y_pct", "perf_3y_pct", "perf_5y_pct", "rank_cat_1y", "rank_cat_3y",
            "rank_cat_5y", "volatility_1y_pct", "max_drawdown_1y_pct",
        ),
    },
}


def _field_profile(frame: pd.DataFrame, requested_fields: tuple[str, ...]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    present = [field for field in requested_fields if field in frame.columns]
    missing_columns = [field for field in requested_fields if field not in frame.columns]
    for field in requested_fields:
        if field not in frame.columns:
            rows.append({"field": field, "column_present": False, "observed": 0, "missing": int(len(frame)), "coverage_pct": 0.0})
            continue
        missing_mask = frame[field].map(is_missing)
        observed = int((~missing_mask).sum())
        rows.append({
            "field": field,
            "column_present": True,
            "observed": observed,
            "missing": int(missing_mask.sum()),
            "coverage_pct": round(observed / len(frame) * 100.0, 2) if len(frame) else 0.0,
        })
    present_cells = len(frame) * len(present)
    observed_cells = sum(row["observed"] for row in rows if row["column_present"])
    summary = {
        "requested_field_count": len(requested_fields),
        "present_field_count": len(present),
        "missing_columns": missing_columns,
        "coverage_pct_present_columns": round(observed_cells / present_cells * 100.0, 2) if present_cells else 0.0,
    }
    return rows, summary


def profile_frame(frame: pd.DataFrame, universe: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    universe = universe.upper()
    if universe not in FIELD_GROUPS:
        raise ValueError(f"UNSUPPORTED_UNIVERSE:{universe}")
    details: list[dict[str, Any]] = []
    group_summary: dict[str, Any] = {}
    for group, fields in FIELD_GROUPS[universe].items():
        rows, summary = _field_profile(frame, fields)
        group_summary[group] = summary
        for row in rows:
            details.append({"universe": universe, "group": group, **row})
    return pd.DataFrame(details), group_summary


def run(root: Path) -> dict[str, Any]:
    actions_legacy = load_master(root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    actions = filter_actions(actions_legacy, root / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts").included
    etf = load_master(root / "inputs" / "V18.2_PEA_ETF_MASTER.csv")
    action_detail, action_summary = profile_frame(actions, "ACTION")
    etf_detail, etf_summary = profile_frame(etf, "ETF")
    detail = pd.concat([action_detail, etf_detail], ignore_index=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "actions_rows": int(len(actions)),
        "etf_rows": int(len(etf)),
        "actions": action_summary,
        "etf": etf_summary,
        "interpretation": "Coverage measures observation presence only. It never upgrades evidence quality and missing values remain missing.",
    }
    outdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    detail.to_csv(outdir / "MASTER_DATA_FIELD_COVERAGE.csv", sep=";", encoding="utf-8-sig", index=False)
    (outdir / "MASTER_DATA_PROFILE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run(Path(__file__).resolve().parents[3])
