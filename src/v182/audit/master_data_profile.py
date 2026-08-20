from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from v182.audit.canonical_universe import filter_actions
from v182.io.frames import is_missing, load_master
from v182.mapping.action_isin_resolver import apply_identity_overlay
from v182.mapping.identity_overlay_store import materialize_identity_overlay

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

# Audit aliases are semantic-equivalence rules only. They improve the truthfulness
# of the coverage report without copying values into legacy columns or changing
# any score. The raw source columns remain preserved in the master/provenance.
FIELD_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "ACTION": {
        "sector": ("sector_yf", "sector_v21", "sector_yahoo"),
        "industry": ("industry_yf", "industry_yahoo"),
        "country": ("country_yf",),
        "exchange": ("exchange_yf", "full_exchange_name_yf", "euronext_mic"),
        "currency": ("currency_yf",),
        "source": ("fundamentals_source", "consensus_source", "ta_source"),
        "target_price": ("target_mean_yf",),
        "n_analysts": ("n_analysts_yf",),
    },
    "ETF": {
        "category": ("category_yf",),
        "official_exchange": ("exchange_yf", "primary_exchange"),
        "max_drawdown_1y_pct": ("max_drawdown_1y",),
    },
}


def _candidate_columns(frame: pd.DataFrame, universe: str, field: str) -> list[str]:
    candidates=(field,)+FIELD_ALIASES.get(universe,{}).get(field,())
    return [candidate for candidate in candidates if candidate in frame.columns]


def _observed_mask(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    if not columns:
        return pd.Series(False,index=frame.index,dtype=bool)
    observed=pd.Series(False,index=frame.index,dtype=bool)
    for column in columns:
        observed |= ~frame[column].map(is_missing)
    return observed


def _field_profile(frame: pd.DataFrame, universe: str, requested_fields: tuple[str, ...]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    semantic_present=0
    observed_cells=0
    missing_columns=[]
    present_denominator_fields=0
    for field in requested_fields:
        columns=_candidate_columns(frame,universe,field)
        if not columns:
            missing_columns.append(field)
            rows.append({
                "field":field,"column_present":False,"resolved_columns":"","observed":0,
                "missing":int(len(frame)),"coverage_pct":0.0,
            })
            continue
        semantic_present += 1
        present_denominator_fields += 1
        mask=_observed_mask(frame,columns)
        observed=int(mask.sum())
        observed_cells += observed
        rows.append({
            "field":field,
            "column_present":True,
            "resolved_columns":"|".join(columns),
            "observed":observed,
            "missing":int(len(frame)-observed),
            "coverage_pct":round(observed/len(frame)*100.0,2) if len(frame) else 0.0,
        })
    present_cells=len(frame)*present_denominator_fields
    requested_cells=len(frame)*len(requested_fields)
    summary={
        "requested_field_count":len(requested_fields),
        "present_field_count":semantic_present,
        "missing_columns":missing_columns,
        "coverage_pct_present_columns":round(observed_cells/present_cells*100.0,2) if present_cells else 0.0,
        "coverage_pct_requested_fields":round(observed_cells/requested_cells*100.0,2) if requested_cells else 0.0,
        "semantic_aliases_enabled":True,
    }
    return rows,summary


def profile_frame(frame: pd.DataFrame, universe: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    universe = universe.upper()
    if universe not in FIELD_GROUPS:
        raise ValueError(f"UNSUPPORTED_UNIVERSE:{universe}")
    details: list[dict[str, Any]] = []
    group_summary: dict[str, Any] = {}
    for group, fields in FIELD_GROUPS[universe].items():
        rows, summary = _field_profile(frame, universe, fields)
        group_summary[group] = summary
        for row in rows:
            details.append({"universe": universe, "group": group, **row})
    return pd.DataFrame(details), group_summary


def run(root: Path) -> dict[str, Any]:
    actions_legacy = load_master(root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    canonical = filter_actions(actions_legacy, root / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts")
    overlay_path = materialize_identity_overlay(root)
    if overlay_path is None:
        actions = canonical.included
        overlay_audit = {"status": "NO_OVERLAY", "applied": 0}
    else:
        actions, overlay_audit = apply_identity_overlay(canonical.included, overlay_path)
    etf = load_master(root / "inputs" / "V18.2_PEA_ETF_MASTER.csv")
    action_detail, action_summary = profile_frame(actions, "ACTION")
    etf_detail, etf_summary = profile_frame(etf, "ETF")
    detail = pd.concat([action_detail, etf_detail], ignore_index=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "actions_rows": int(len(actions)),
        "etf_rows": int(len(etf)),
        "action_identity_overlay": overlay_audit,
        "actions": action_summary,
        "etf": etf_summary,
        "interpretation": "Coverage measures semantic observation presence through governed equivalent aliases. It never copies values, upgrades evidence quality, changes scoring, or imputes missing data.",
    }
    outdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    detail.to_csv(outdir / "MASTER_DATA_FIELD_COVERAGE.csv", sep=";", encoding="utf-8-sig", index=False)
    (outdir / "MASTER_DATA_PROFILE.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run(Path(__file__).resolve().parents[3])
