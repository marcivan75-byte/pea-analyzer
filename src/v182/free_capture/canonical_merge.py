from __future__ import annotations

from datetime import datetime, timezone
import json
import math

import pandas as pd

from .core import CaptureStore, is_observed, write_csv


MERGE_FIELDS = [
    "roe_v21_pct",
    "roa_v21_pct",
    "operating_margin_v21_pct",
    "revenue_growth_v21_pct",
    "earnings_growth_v21_pct",
    "debt_to_ebitda_v21",
    "free_cash_flow_v21",
    "pb_v21",
    "fcf_yield_v21",
]
MAX_ANNUAL_AGE_DAYS = 730
STALE_WARNING_DAYS = 450


def _coverage(df: pd.DataFrame, cfg: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    groups = {
        "market": cfg["key_fields"]["market"],
        "fundamentals": cfg["key_fields"]["fundamentals"],
        "valuation": cfg["key_fields"]["valuation"],
        "prospective": cfg["key_fields"]["prospective"],
    }
    for group, fields in groups.items():
        per_row = pd.Series(0.0, index=df.index)
        for field in fields:
            if field in df:
                per_row += df[field].map(is_observed).astype(float)
        frac = per_row / max(1, len(fields))
        out[f"{group}_mean_field_coverage_pct"] = round(float(frac.mean() * 100), 2)
        out[f"{group}_adequate_rows_pct"] = round(float(frac.ge(0.75).mean() * 100), 2)
    return out


def merge(base: pd.DataFrame, store: CaptureStore, cfg: dict) -> tuple[pd.DataFrame, dict]:
    out = base.copy()
    facts = store.facts()
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    audit = {
        "passed": True,
        "policy": "MISSING_ONLY_VALIDATED_ESEF_NO_OVERWRITE",
        "fields_allowed": MERGE_FIELDS,
        "applied_cells": 0,
        "applied_rows": 0,
        "skipped_existing": 0,
        "skipped_stale": 0,
        "skipped_invalid": 0,
        "stale_warning_cells": 0,
        "field_applied": {},
        "overwrites": 0,
    }

    if facts.empty:
        out["v211_free_merge_applied_count"] = 0
        out["v211_free_merge_status"] = "NO_VALIDATED_FREE_FACT"
        audit["coverage_after_merge"] = _coverage(out, cfg)
        return out, audit

    v = facts[
        facts["source"].astype(str).eq("INTERNAL_FROM_ESEF")
        & facts["status"].astype(str).eq("VALIDATED_DERIVED")
        & facts["field"].astype(str).isin(MERGE_FIELDS)
    ].copy()
    if v.empty:
        out["v211_free_merge_applied_count"] = 0
        out["v211_free_merge_status"] = "NO_VALIDATED_FREE_FACT"
        audit["coverage_after_merge"] = _coverage(out, cfg)
        return out, audit

    v["_asof"] = pd.to_datetime(v["as_of"], errors="coerce")
    v["_value"] = pd.to_numeric(v["value"], errors="coerce")
    v = v.dropna(subset=["_asof", "_value"])
    v = v[v["_value"].map(math.isfinite)]
    v = v.sort_values(["isin", "field", "_asof"], ascending=[True, True, False])
    v = v.drop_duplicates(["isin", "field"], keep="first")

    idx_by_isin = {str(x): i for i, x in enumerate(out["isin"].astype(str))}
    applied_per_row = pd.Series(0, index=out.index, dtype=int)

    for _, fact in v.iterrows():
        isin = str(fact["isin"])
        field = str(fact["field"])
        if isin not in idx_by_isin or field not in out:
            audit["skipped_invalid"] += 1
            continue
        i = idx_by_isin[isin]
        if is_observed(out.at[i, field]):
            audit["skipped_existing"] += 1
            continue

        age_days = int((now - fact["_asof"]).days)
        if age_days < -7 or age_days > MAX_ANNUAL_AGE_DAYS:
            audit["skipped_stale"] += 1
            continue

        before = out.at[i, field]
        if is_observed(before):
            audit["overwrites"] += 1
            continue

        out.at[i, field] = fact["_value"]
        out.at[i, f"v211_{field}_source"] = "INTERNAL_FROM_ESEF"
        out.at[i, f"v211_{field}_as_of"] = str(fact["as_of"])
        out.at[i, f"v211_{field}_confidence"] = float(fact.get("confidence") or 0.90)
        out.at[i, f"v211_{field}_freshness"] = "STALE_WARNING" if age_days > STALE_WARNING_DAYS else "CURRENT_ANNUAL"
        if age_days > STALE_WARNING_DAYS:
            audit["stale_warning_cells"] += 1

        applied_per_row.at[i] += 1
        audit["applied_cells"] += 1
        audit["field_applied"][field] = int(audit["field_applied"].get(field, 0)) + 1

    out["v211_free_merge_applied_count"] = applied_per_row
    out["v211_free_merge_status"] = "PRESERVED_BASE"
    out.loc[applied_per_row.gt(0), "v211_free_merge_status"] = "APPLIED_VALIDATED_FREE"
    audit["applied_rows"] = int(applied_per_row.gt(0).sum())
    audit["coverage_after_merge"] = _coverage(out, cfg)
    audit["passed"] = audit["overwrites"] == 0
    return out, audit


def write_merged(base: pd.DataFrame, store: CaptureStore, cfg: dict) -> dict:
    merged, audit = merge(base, store, cfg)
    output = store.root / "V21.1_ACTIONS_PEA_REFERENCE_MERGED.csv"
    write_csv(merged, output, ["isin"])
    audit["output"] = str(output)
    audit["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    (store.root / "V21.1_CANONICAL_MERGE_AUDIT.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return audit
