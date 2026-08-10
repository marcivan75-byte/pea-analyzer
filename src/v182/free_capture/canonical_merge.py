from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os

import pandas as pd

from .core import CaptureStore, is_observed, load_config, load_universe, write_csv


MERGE_POLICIES = {
    "INTERNAL_FROM_ESEF": {
        "status": "VALIDATED_DERIVED",
        "max_age_days": 730,
        "stale_warning_days": 450,
        "fields": [
            "roe_v21_pct",
            "roa_v21_pct",
            "operating_margin_v21_pct",
            "revenue_growth_v21_pct",
            "earnings_growth_v21_pct",
            "debt_to_ebitda_v21",
            "free_cash_flow_v21",
            "pb_v21",
            "fcf_yield_v21",
        ],
    },
    "ZONEBOURSE_PUBLIC_V3": {
        "status": "OBSERVED_VALIDATED_ISIN_PRICE",
        "max_age_days": 14,
        "stale_warning_days": 7,
        "fields": [
            "target_mean_v21",
            "target_high_v21",
            "target_low_v21",
            "n_analysts_v21",
            "consensus_score_100_v21",
        ],
    },
    "BOURSORAMA_PUBLIC_V2": {
        "status": "OBSERVED_VALIDATED_ISIN",
        "max_age_days": 14,
        "stale_warning_days": 7,
        "fields": [
            "per_forward_v21",
            "n_analysts_v21",
            "consensus_score_100_v21",
            "consensus_delta_4w",
            "next_earnings_date",
        ],
    },
}


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


def _valid_value(field: str, value: object, value_text: object, now: pd.Timestamp) -> object | None:
    if field == "next_earnings_date":
        raw = str(value_text).strip() if is_observed(value_text) else str(value).strip()
        d = pd.to_datetime(raw, errors="coerce")
        if pd.isna(d):
            return None
        delta = (d - now).days
        if delta < -3 or delta > 550:
            return None
        return d.date().isoformat()

    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    bounds = {
        "per_forward_v21": (0.01, 250.0),
        "n_analysts_v21": (0.0, 100.0),
        "consensus_score_100_v21": (0.0, 100.0),
        "consensus_delta_4w": (-100.0, 100.0),
        "target_mean_v21": (0.0001, 1.0e7),
        "target_high_v21": (0.0001, 1.0e7),
        "target_low_v21": (0.0001, 1.0e7),
        "roe_v21_pct": (-300.0, 300.0),
        "roa_v21_pct": (-150.0, 150.0),
        "operating_margin_v21_pct": (-150.0, 150.0),
        "revenue_growth_v21_pct": (-100.0, 1000.0),
        "earnings_growth_v21_pct": (-500.0, 1000.0),
        "debt_to_ebitda_v21": (0.0, 50.0),
        "pb_v21": (0.0, 100.0),
        "fcf_yield_v21": (-100.0, 100.0),
    }
    if field in bounds:
        lo, hi = bounds[field]
        if not (lo <= x <= hi):
            return None
    return x


def merge(base: pd.DataFrame, store: CaptureStore, cfg: dict) -> tuple[pd.DataFrame, dict]:
    out = base.copy()
    facts = store.facts()
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    audit = {
        "passed": True,
        "policy": "MISSING_ONLY_VALIDATED_FREE_NO_OVERWRITE",
        "sources_allowed": list(MERGE_POLICIES),
        "applied_cells": 0,
        "applied_rows": 0,
        "skipped_existing": 0,
        "skipped_stale": 0,
        "skipped_invalid": 0,
        "stale_warning_cells": 0,
        "field_applied": {},
        "source_applied": {},
        "overwrites": 0,
    }

    if facts.empty:
        out["v211_free_merge_applied_count"] = 0
        out["v211_free_merge_status"] = "NO_VALIDATED_FREE_FACT"
        audit["coverage_after_merge"] = _coverage(out, cfg)
        return out, audit

    chunks = []
    for source, policy in MERGE_POLICIES.items():
        x = facts[
            facts["source"].astype(str).eq(source)
            & facts["status"].astype(str).eq(policy["status"])
            & facts["field"].astype(str).isin(policy["fields"])
        ].copy()
        if not x.empty:
            x["_source_policy"] = source
            chunks.append(x)
    if not chunks:
        out["v211_free_merge_applied_count"] = 0
        out["v211_free_merge_status"] = "NO_VALIDATED_FREE_FACT"
        audit["coverage_after_merge"] = _coverage(out, cfg)
        return out, audit

    v = pd.concat(chunks, ignore_index=True)
    v["_asof"] = pd.to_datetime(v["as_of"], errors="coerce")
    v = v.dropna(subset=["_asof"])
    source_priority = {"INTERNAL_FROM_ESEF": 1, "ZONEBOURSE_PUBLIC_V3": 2, "BOURSORAMA_PUBLIC_V2": 3}
    v["_p"] = v["_source_policy"].map(source_priority).fillna(9)
    v = v.sort_values(["isin", "field", "_asof", "_p"], ascending=[True, True, False, True])
    v = v.drop_duplicates(["isin", "field"], keep="first")

    idx_by_isin = {str(x): i for i, x in enumerate(out["isin"].astype(str))}
    applied_per_row = pd.Series(0, index=out.index, dtype=int)

    for _, fact in v.iterrows():
        isin = str(fact["isin"])
        field = str(fact["field"])
        source = str(fact["_source_policy"])
        policy = MERGE_POLICIES[source]
        if isin not in idx_by_isin or field not in out:
            audit["skipped_invalid"] += 1
            continue
        i = idx_by_isin[isin]
        if is_observed(out.at[i, field]):
            audit["skipped_existing"] += 1
            continue

        age_days = int((now - fact["_asof"]).days)
        if age_days < -7 or age_days > int(policy["max_age_days"]):
            audit["skipped_stale"] += 1
            continue

        candidate = _valid_value(field, fact.get("value"), fact.get("value_text"), now)
        if candidate is None:
            audit["skipped_invalid"] += 1
            continue

        before = out.at[i, field]
        if is_observed(before):
            audit["overwrites"] += 1
            continue

        out.at[i, field] = candidate
        out.at[i, f"v211_{field}_source"] = source
        out.at[i, f"v211_{field}_as_of"] = str(fact["as_of"])
        out.at[i, f"v211_{field}_confidence"] = float(fact.get("confidence") or 0.80)
        out.at[i, f"v211_{field}_freshness"] = (
            "STALE_WARNING" if age_days > int(policy["stale_warning_days"]) else "CURRENT"
        )
        if age_days > int(policy["stale_warning_days"]):
            audit["stale_warning_cells"] += 1

        applied_per_row.at[i] += 1
        audit["applied_cells"] += 1
        audit["field_applied"][field] = int(audit["field_applied"].get(field, 0)) + 1
        audit["source_applied"][source] = int(audit["source_applied"].get(source, 0)) + 1

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


ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    cfg = load_config()
    input_path = Path(os.getenv("V211_INPUT", str(ROOT / "outputs/V21.0_ACTIONS_PEA_REFERENCE_MASTER.csv")))
    store = CaptureStore(Path(os.getenv("V211_STORE", str(ROOT / cfg["cache"]["root"]))))
    base = load_universe(input_path)
    audit = write_merged(base, store, cfg)
    if not audit.get("passed"):
        raise RuntimeError(f"V21.1 canonical merge gate failed: {audit}")
    print("V21_1_CANONICAL_MERGE_OK", json.dumps({
        "applied_cells": audit["applied_cells"],
        "applied_rows": audit["applied_rows"],
        "overwrites": audit["overwrites"],
        "field_applied": audit["field_applied"],
        "source_applied": audit["source_applied"],
        "coverage_after_merge": audit["coverage_after_merge"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
