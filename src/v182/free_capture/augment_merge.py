from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import pandas as pd

from .core import CaptureStore, is_observed


POLICIES = {
    "FINNHUB_FREE": {
        "status": "OBSERVED_FREE",
        "max_age_days": 35,
        "stale_warning_days": 10,
        "fields": {
            "per_ttm_v21", "pb_v21", "roe_v21_pct", "roa_v21_pct", "roic_v21_pct",
            "operating_margin_v21_pct", "net_margin_v21_pct", "revenue_growth_v21_pct",
            "revenue_cagr_5y_v21_pct", "earnings_growth_v21_pct", "ev_to_ebitda_v21",
            "debt_to_equity_v21", "debt_to_ebitda_v21", "current_ratio_v21",
            "interest_coverage_v21", "dividend_yield_v21_pct", "beta_v21",
            "high_52w", "low_52w", "fcf_yield_v21", "target_mean_v21",
            "target_low_v21", "target_high_v21", "target_median_v21", "n_analysts_v21",
            "consensus_score_100_v21", "consensus_score_100_4w_ago_v21", "consensus_delta_4w",
            "upgrades_30d_v21", "downgrades_30d_v21", "net_upgrades_30d_v21",
            "broker_weighted_revision_30d",
        },
    },
    "INTERNAL_FROM_FREE_OHLCV": {
        "status": "DERIVED",
        "max_age_days": 5,
        "stale_warning_days": 2,
        "fields": {
            "last_close", "volume", "volume_avg_20d", "high_52w", "low_52w",
            "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "perf_1y_pct",
            "mm20", "mm50", "mm100", "mm200", "rsi14", "stoch_k", "stoch_d",
            "macd_line", "macd_signal", "macd_hist", "atr14", "bollinger_mid",
            "bollinger_upper", "bollinger_lower", "bollinger_width_pct", "sharpe_1y_rf0",
            "volatility_20d", "volatility_60d", "volatility_1y_pct", "max_drawdown_1y", "rvol20",
        },
    },
    "EURONEXT_LIVE_PUBLIC": {
        "status": "OBSERVED_VALIDATED_ISIN",
        "max_age_days": 5,
        "stale_warning_days": 2,
        "fields": {
            "euronext_live_last_price", "euronext_live_bid", "euronext_live_ask",
            "free_float_pct", "spread_pct",
        },
    },
    "AMF_SHORT_POSITIONS": {
        "status": "OBSERVED_REGULATORY",
        "max_age_days": 10,
        "stale_warning_days": 3,
        "fields": {
            "amf_public_net_short_pct", "amf_public_net_short_max_holder_pct",
            "amf_public_net_short_holders", "amf_public_net_short_latest_date",
        },
    },
}

TEXT_FIELDS = {"amf_public_net_short_latest_date"}

BOUNDS = {
    "rsi14": (0, 100), "stoch_k": (0, 100), "stoch_d": (0, 100),
    "free_float_pct": (0, 100), "spread_pct": (0, 100),
    "amf_public_net_short_pct": (0, 100), "amf_public_net_short_max_holder_pct": (0, 100),
    "amf_public_net_short_holders": (0, 10000), "consensus_score_100_v21": (0, 100),
    "consensus_score_100_4w_ago_v21": (0, 100), "per_ttm_v21": (0, 500),
    "pb_v21": (0, 200), "ev_to_ebitda_v21": (-100, 500),
    "atr14": (0, 1e9), "bollinger_width_pct": (0, 1000),
    "volatility_20d": (0, 1000), "volatility_60d": (0, 1000), "volatility_1y_pct": (0, 1000),
    "sharpe_1y_rf0": (-20, 20),
}


def _number(value: object) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _candidate(field: str, fact: pd.Series) -> object | None:
    if field in TEXT_FIELDS:
        value = fact.get("value_text") if is_observed(fact.get("value_text")) else fact.get("value")
        text = str(value or "").strip()
        if field.endswith("_date"):
            parsed = pd.to_datetime(text, errors="coerce")
            if pd.isna(parsed):
                return None
            return parsed.date().isoformat()
        return text or None
    value = _number(fact.get("value"))
    if value is None:
        return None
    if field in BOUNDS:
        lo, hi = BOUNDS[field]
        if not (lo <= value <= hi):
            return None
    return value


def apply(merged_path: Path, store: CaptureStore) -> dict:
    df = pd.read_csv(merged_path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if "isin" not in df.columns or df["isin"].astype(str).duplicated().any():
        raise RuntimeError("Complementary merge requires unique ISIN")
    facts = store.facts()
    audit = {
        "passed": True,
        "policy": "MISSING_ONLY_COMPLEMENTARY_FREE_NO_OVERWRITE",
        "applied_cells": 0,
        "applied_rows": 0,
        "created_fields": [],
        "overwrites": 0,
        "skipped_existing": 0,
        "skipped_stale": 0,
        "skipped_invalid": 0,
        "field_applied": {},
        "source_applied": {},
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if facts.empty:
        return audit

    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    frames = []
    for source, policy in POLICIES.items():
        x = facts[
            facts["source"].astype(str).eq(source)
            & facts["status"].astype(str).eq(policy["status"])
            & facts["field"].astype(str).isin(policy["fields"])
        ].copy()
        if not x.empty:
            x["_source"] = source
            frames.append(x)
    if not frames:
        return audit

    facts = pd.concat(frames, ignore_index=True)
    facts["_asof"] = pd.to_datetime(facts["as_of"], errors="coerce")
    facts = facts.dropna(subset=["_asof"]).sort_values(["isin", "field", "_asof"], ascending=[True, True, False])
    facts = facts.drop_duplicates(["isin", "field"], keep="first")
    row_by_isin = {str(v): i for i, v in enumerate(df["isin"].astype(str))}
    touched: set[int] = set()

    for _, fact in facts.iterrows():
        isin = str(fact.get("isin") or "")
        field = str(fact.get("field") or "")
        source = str(fact.get("_source") or "")
        if isin not in row_by_isin:
            audit["skipped_invalid"] += 1
            continue
        if field not in df.columns:
            df[field] = pd.NA
            audit["created_fields"].append(field)
        i = row_by_isin[isin]
        if is_observed(df.at[i, field]):
            audit["skipped_existing"] += 1
            continue
        policy = POLICIES[source]
        age_days = int((now - fact["_asof"]).days)
        if age_days < -7 or age_days > int(policy["max_age_days"]):
            audit["skipped_stale"] += 1
            continue
        value = _candidate(field, fact)
        if value is None:
            audit["skipped_invalid"] += 1
            continue
        if is_observed(df.at[i, field]):
            audit["overwrites"] += 1
            continue

        df.at[i, field] = value
        for suffix, val in {
            "source": source,
            "as_of": str(fact.get("as_of") or ""),
            "confidence": _number(fact.get("confidence")) or 0.80,
            "freshness": "STALE_WARNING" if age_days > int(policy["stale_warning_days"]) else "CURRENT",
        }.items():
            col = f"v211_{field}_{suffix}"
            if col not in df.columns:
                df[col] = pd.NA
            df.at[i, col] = val
        audit["applied_cells"] += 1
        audit["field_applied"][field] = int(audit["field_applied"].get(field, 0)) + 1
        audit["source_applied"][source] = int(audit["source_applied"].get(source, 0)) + 1
        touched.add(i)

    audit["created_fields"] = sorted(set(audit["created_fields"]))
    audit["applied_rows"] = len(touched)
    audit["passed"] = audit["overwrites"] == 0
    df.to_csv(merged_path, sep=";", index=False, encoding="utf-8-sig")
    audit_path = store.root / "V21.1_COMPLEMENTARY_MERGE_AUDIT.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit
