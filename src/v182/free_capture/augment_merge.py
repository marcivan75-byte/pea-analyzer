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
            "pb_v21", "roe_v21_pct", "roa_v21_pct", "operating_margin_v21_pct",
            "net_margin_v21_pct", "revenue_growth_v21_pct", "earnings_growth_v21_pct",
            "debt_to_equity_v21", "debt_to_ebitda_v21", "current_ratio_v21",
            "interest_coverage_v21", "dividend_yield_v21_pct", "beta_v21",
            "high_52w", "low_52w", "fcf_yield_v21", "target_mean_v21",
            "n_analysts_v21", "consensus_score_100_v21",
        },
    },
    "INTERNAL_FROM_FREE_OHLCV": {
        "status": "DERIVED",
        "max_age_days": 5,
        "stale_warning_days": 2,
        "fields": {
            "last_close", "high_52w", "low_52w", "perf_1m_pct", "perf_3m_pct",
            "perf_6m_pct", "perf_1y_pct", "mm20", "mm50", "mm200", "rsi14",
            "volatility_20d", "volatility_60d", "max_drawdown_1y", "rvol20",
        },
    },
}


def _number(value: object) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


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
        if isin not in row_by_isin or field not in df.columns:
            audit["skipped_invalid"] += 1
            continue
        i = row_by_isin[isin]
        if is_observed(df.at[i, field]):
            audit["skipped_existing"] += 1
            continue
        policy = POLICIES[source]
        age_days = int((now - fact["_asof"]).days)
        if age_days < -7 or age_days > int(policy["max_age_days"]):
            audit["skipped_stale"] += 1
            continue
        value = _number(fact.get("value"))
        if value is None:
            audit["skipped_invalid"] += 1
            continue
        if is_observed(df.at[i, field]):
            audit["overwrites"] += 1
            continue

        df.at[i, field] = value
        df.at[i, f"v211_{field}_source"] = source
        df.at[i, f"v211_{field}_as_of"] = str(fact.get("as_of") or "")
        df.at[i, f"v211_{field}_confidence"] = _number(fact.get("confidence")) or 0.80
        df.at[i, f"v211_{field}_freshness"] = "STALE_WARNING" if age_days > int(policy["stale_warning_days"]) else "CURRENT"
        audit["applied_cells"] += 1
        audit["field_applied"][field] = int(audit["field_applied"].get(field, 0)) + 1
        audit["source_applied"][source] = int(audit["source_applied"].get(source, 0)) + 1
        touched.add(i)

    audit["applied_rows"] = len(touched)
    audit["passed"] = audit["overwrites"] == 0
    df.to_csv(merged_path, sep=";", index=False, encoding="utf-8-sig")
    audit_path = store.root / "V21.1_COMPLEMENTARY_MERGE_AUDIT.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit
