from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

import pandas as pd

from .core import CaptureStore, is_observed, load_config, load_universe, priority_frame, write_csv
from .openfigi_v3 import capture as capture_openfigi
from .gleif_bulk import capture as capture_gleif_bulk
from .gleif_lei import capture as capture_gleif
from .esef_xbrl import capture as capture_esef
from .official_delayed import capture as capture_official
from .manual_imports import capture as capture_manual
from .twelvedata_free import capture as capture_twelve
from .alpha_free import capture as capture_alpha
from .marketstack_free import capture as capture_marketstack
from .derive_market import capture as derive_market
from .derive_fundamentals import capture as derive_fundamentals
from .canonical_merge import write_merged

ROOT = Path(__file__).resolve().parents[3]
INPUT = Path(os.getenv("V211_INPUT", str(ROOT / "outputs/V21.0_ACTIONS_PEA_REFERENCE_MASTER.csv")))
IMPORT_ROOT = Path(os.getenv("V211_MANUAL_IMPORT_ROOT", str(ROOT / "data/import/free_capture")))

REGULATED_MICS = {"XPAR", "XAMS", "XBRU", "XLIS", "MTAA", "XOSL", "XMAD", "XSTO", "XHEL", "XCSE"}
REGULATED_COUNTRIES = {"FRANCE", "NETHERLANDS", "BELGIUM", "PORTUGAL", "ITALY", "NORWAY", "SPAIN", "SWEDEN", "FINLAND", "DENMARK"}
DEEP_FUNDAMENTAL_FIELDS = [
    "roe_v21_pct",
    "roa_v21_pct",
    "operating_margin_v21_pct",
    "net_margin_v21_pct",
    "gross_margin_v21_pct",
    "current_ratio_v21",
    "interest_coverage_v21",
    "revenue_growth_v21_pct",
    "earnings_growth_v21_pct",
    "ebitda_v21",
    "total_debt_v21",
    "debt_to_equity_v21",
    "debt_to_ebitda_v21",
    "free_cash_flow_v21",
    "pb_v21",
    "fcf_yield_v21",
]


def _observed_series(s: pd.Series) -> pd.Series:
    return s.map(is_observed)


def _regulated_priority(base: pd.DataFrame, missing_priority: pd.DataFrame) -> pd.DataFrame:
    x = base.copy()
    mic = x.get("euronext_mic", pd.Series("", index=x.index)).astype(str).str.upper()
    country = x.get("country", pd.Series("", index=x.index)).astype(str).str.upper()
    x["_regulated"] = mic.isin(REGULATED_MICS) | (country.isin(REGULATED_COUNTRIES) & ~mic.isin({"ALXP", "ALXB", "XMLI", "EXGM", "MERK", "MLXB", "MTAH", "ENXL", "XESM"}))
    x["_mc"] = pd.to_numeric(x.get("market_cap_v21"), errors="coerce").fillna(-1)
    x["_volume"] = pd.to_numeric(x.get("volume"), errors="coerce").fillna(-1)
    x["_mt"] = pd.to_numeric(x.get("score_mt"), errors="coerce").fillna(0)
    x["_lt"] = pd.to_numeric(x.get("score_lt"), errors="coerce").fillna(0)
    x = x.sort_values(["_regulated", "_mc", "_volume", "_mt", "_lt"], ascending=[False, False, False, False, False], kind="stable")
    seen = set(x["isin"].astype(str))
    tail = missing_priority.loc[~missing_priority["isin"].astype(str).isin(seen)]
    return pd.concat([x, tail], ignore_index=True)


def _materialize(base: pd.DataFrame, store: CaptureStore, cfg: dict) -> tuple[pd.DataFrame, dict]:
    out = base.copy()
    facts = store.facts()
    source_priority = {
        "INTERNAL_FROM_ESEF": 1,
        "ESEF_XBRL_JSON": 2,
        "INTERNAL_FROM_FREE_OHLCV": 2,
        "ALPHA_VANTAGE_FREE_PROXY": 3,
        "ALPHA_VANTAGE_FREE": 4,
        "TWELVEDATA_FREE": 5,
        "MARKETSTACK_FREE": 6,
    }
    if not facts.empty:
        facts = facts.copy()
        facts["_p"] = facts["source"].map(source_priority).fillna(9)
        facts["_asof"] = pd.to_datetime(facts["as_of"], errors="coerce")
        best = facts.sort_values(["isin", "field", "_asof", "_p"], ascending=[True, True, False, True])
        best = best.drop_duplicates(["isin", "field"], keep="first")
        idx = out["isin"].astype(str)
        for field in sorted(set(best["field"].astype(str))):
            sub = best[best["field"].astype(str).eq(field)].set_index("isin")
            out[f"free_{field}"] = idx.map(sub["value"].to_dict())
            out[f"free_{field}_source"] = idx.map(sub["source"].to_dict())
            out[f"free_{field}_as_of"] = idx.map(sub["as_of"].to_dict())

    identity = store.identity()
    if not identity.empty:
        idx = out["isin"].astype(str)
        id_priority = {"GLEIF_ISIN_LEI_BULK": 1, "GLEIF_ISIN_LEI": 2, "OPENFIGI_V3": 3}
        identity = identity.copy()
        identity["_p"] = identity["source"].map(id_priority).fillna(9)
        for f in ["figi", "composite_figi", "share_class_figi", "ticker", "exchange", "mic", "lei", "lei_source"]:
            vals = identity[identity[f].map(is_observed)].sort_values(["isin", "_p"]).drop_duplicates("isin", keep="first")
            out[f"free_identity_{f}"] = idx.map(vals.set_index("isin")[f].to_dict()) if not vals.empty else ""
        statuses = identity.sort_values(["isin", "_p"]).groupby("isin")["resolution_status"].apply(lambda x: "|".join(sorted(set(map(str, x)))))
        out["free_identity_resolution_status"] = idx.map(statuses.to_dict())

    metrics: dict[str, float] = {}
    groups = {
        "market": cfg["key_fields"]["market"],
        "fundamentals": cfg["key_fields"]["fundamentals"],
        "valuation": cfg["key_fields"]["valuation"],
        "prospective": cfg["key_fields"]["prospective"],
    }
    for group, fields in groups.items():
        per_row = pd.Series(0.0, index=out.index)
        base_count = pd.Series(0.0, index=out.index)
        free_increment = pd.Series(0.0, index=out.index)
        for field in fields:
            base_ok = _observed_series(out[field]) if field in out else pd.Series(False, index=out.index)
            free_col = f"free_{field}"
            free_ok = _observed_series(out[free_col]) if free_col in out else pd.Series(False, index=out.index)
            base_count += base_ok.astype(float)
            free_increment += ((~base_ok) & free_ok).astype(float)
            per_row += (base_ok | free_ok).astype(float)
        denom = max(1, len(fields))
        frac = per_row / denom
        out[f"free_capture_{group}_coverage_pct"] = (frac * 100).round(2)
        metrics[f"{group}_base_mean_field_coverage_pct"] = round(float((base_count / denom).mean() * 100), 2)
        metrics[f"{group}_free_increment_points"] = round(float((free_increment / denom).mean() * 100), 2)
        metrics[f"{group}_base_plus_free_mean_field_coverage_pct"] = round(float(frac.mean() * 100), 2)
        metrics[f"{group}_adequate_rows_pct"] = round(float(frac.ge(0.75).mean() * 100), 2)
    return out, metrics


def _deep_audit(base: pd.DataFrame, merged: pd.DataFrame, store: CaptureStore) -> dict:
    per_field: dict[str, dict[str, float]] = {}
    base_total = 0.0
    merged_total = 0.0
    for field in DEEP_FUNDAMENTAL_FIELDS:
        base_ok = base[field].map(is_observed) if field in base else pd.Series(False, index=base.index)
        merged_ok = merged[field].map(is_observed) if field in merged else pd.Series(False, index=merged.index)
        base_count = int(base_ok.sum())
        merged_count = int(merged_ok.sum())
        b = float(base_count / max(1, len(base)) * 100.0)
        m = float(merged_count / max(1, len(merged)) * 100.0)
        per_field[field] = {
            "base_pct": round(b, 2),
            "merged_pct": round(m, 2),
            "gain_points": round(m - b, 2),
            "base_cells": base_count,
            "merged_cells": merged_count,
            "new_cells": max(0, merged_count - base_count),
        }
        base_total += b
        merged_total += m

    facts = store.facts()
    if facts.empty:
        schema_v2 = positives = negatives = set()
    else:
        schema_v2 = set(facts.loc[
            facts["source"].astype(str).eq("ESEF_SCHEMA")
            & facts["field"].astype(str).eq("esef_capture_schema")
            & facts["status"].astype(str).eq("SCHEMA_V2_COMPLETE"),
            "isin",
        ].astype(str))
        positives = set(facts.loc[facts["source"].astype(str).eq("ESEF_XBRL_JSON"), "isin"].astype(str))
        negatives = set(facts.loc[
            facts["source"].astype(str).eq("ESEF_DISCOVERY")
            & facts["status"].astype(str).isin({"NO_FILING", "NO_CANONICAL_CONCEPTS"}),
            "isin",
        ].astype(str))

    return {
        "fields": DEEP_FUNDAMENTAL_FIELDS,
        "base_mean_field_coverage_pct": round(base_total / len(DEEP_FUNDAMENTAL_FIELDS), 2),
        "merged_mean_field_coverage_pct": round(merged_total / len(DEEP_FUNDAMENTAL_FIELDS), 2),
        "gain_points": round((merged_total - base_total) / len(DEEP_FUNDAMENTAL_FIELDS), 2),
        "per_field": per_field,
        "esef_positive_isin": len(positives),
        "esef_negative_isin": len(negatives),
        "esef_classified_isin": len(positives | negatives),
        "esef_schema_v2_completed_isin": len(schema_v2),
        "esef_schema_v2_remaining_positive_isin": max(0, len(positives) - len(schema_v2)),
    }


def _queues(prioritized: pd.DataFrame, regulated: pd.DataFrame, store: CaptureStore) -> None:
    qroot = store.root / "queues"
    qroot.mkdir(parents=True, exist_ok=True)
    cols = [c for c in ["isin", "name", "country", "euronext_symbol", "euronext_mic", "yahoo_ticker", "market_cap_v21", "score_mt", "score_lt", "free_capture_priority"] if c in prioritized]
    write_csv(regulated.head(600)[[c for c in cols if c in regulated]], qroot / "V21.1_ESEF_ISSUER_QUEUE.csv", ["isin"])
    public = prioritized.head(250)[cols].copy()
    public["boursorama_status"] = "PENDING_TARGETED_CAPTURE_VALIDATION"
    public["zonebourse_status"] = "PENDING_TARGETED_CAPTURE_VALIDATION"
    public["capture_policy"] = "TARGETED_ONLY_CACHE_WEEKLY"
    write_csv(public, qroot / "V21.1_PUBLIC_PROSPECTIVE_QUEUE.csv", ["free_capture_priority"])


def main() -> None:
    cfg = load_config()
    if not cfg.get("free_only") or cfg.get("execution_mode") != "RESEARCH_ONLY":
        raise RuntimeError("V21.1 FREE_ONLY safety gate")
    base = load_universe(INPUT)
    prioritized = priority_frame(base, cfg)
    regulated = _regulated_priority(base, prioritized)
    store = CaptureStore(Path(os.getenv("V211_STORE", str(ROOT / cfg["cache"]["root"]))))
    store.root.mkdir(parents=True, exist_ok=True)

    results = {}
    results["openfigi"] = capture_openfigi(base, store, int(os.getenv("V211_OPENFIGI_MAX_REQUESTS", "30")))
    results["gleif_bulk"] = capture_gleif_bulk(base, store)
    results["gleif_api"] = capture_gleif(
        regulated, store,
        max_symbols=int(os.getenv("V211_GLEIF_MAX_SYMBOLS", "300")),
        workers=int(os.getenv("V211_GLEIF_WORKERS", "3")),
    )
    results["esef"] = capture_esef(regulated, store, int(os.getenv("V211_ESEF_MAX_SYMBOLS", "50")))
    results["derived_fundamentals"] = derive_fundamentals(base, store)

    results["official_delayed"] = capture_official(store)
    results["manual_imports"] = capture_manual(base, store, IMPORT_ROOT)

    twelve_max = int(os.getenv("V211_TWELVE_MAX_SYMBOLS", "5"))
    alpha_max = int(os.getenv("V211_ALPHA_MAX_SYMBOLS", "3"))
    marketstack_max = int(os.getenv("V211_MARKETSTACK_MAX_SYMBOLS", "1"))
    results["twelvedata"] = capture_twelve(
        prioritized, store, max_symbols=twelve_max,
        credit_guard=int(os.getenv("V211_TWELVE_CREDIT_GUARD", "760")),
    ) if twelve_max > 0 else {"status": "DISABLED_THIS_WAVE"}
    results["marketstack"] = capture_marketstack(prioritized, store, marketstack_max) if marketstack_max > 0 else {"status": "DISABLED_THIS_WAVE"}
    results["alpha_vantage"] = capture_alpha(
        prioritized, store, max_symbols=alpha_max,
        max_calls=int(os.getenv("V211_ALPHA_MAX_CALLS", "23")),
    ) if alpha_max > 0 else {"status": "DISABLED_THIS_WAVE"}

    results["derived_market"] = derive_market(store)
    results["final_canonical_merge"] = write_merged(base, store, cfg)

    overlay, metrics = _materialize(base, store, cfg)
    _queues(prioritized, regulated, store)
    write_csv(overlay, store.root / "V21.1_FREE_CAPTURE_OVERLAY.csv", ["isin"])

    merged_path = store.root / "V21.1_ACTIONS_PEA_REFERENCE_MERGED.csv"
    merged = pd.read_csv(merged_path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    deep_coverage = _deep_audit(base, merged, store)
    (store.root / "V21.1_DEEP_FUNDAMENTAL_AUDIT.json").write_text(
        json.dumps(deep_coverage, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    health = store.health()
    source_status = health.groupby("source").tail(1).set_index("source")["status"].to_dict() if not health.empty else {}
    identity = store.identity()
    identity_isins = set(identity["isin"].astype(str)) if not identity.empty else set()
    lei_isins = set(identity.loc[identity["lei"].astype(str).str.len().eq(20), "isin"].astype(str)) if not identity.empty else set()
    market = store.market()
    market_isins = set(market["isin"].astype(str)) if not market.empty else set()
    audit = {
        "passed": True,
        "version": cfg["version"],
        "execution": cfg["execution_mode"],
        "free_only": True,
        "rows": len(base),
        "unique_isin": base["isin"].astype(str).nunique(),
        "cache_identity_isin": len(identity_isins),
        "cache_lei_isin": len(lei_isins),
        "cache_market_isin": len(market_isins),
        "cache_market_rows": len(market),
        "cache_fact_rows": len(store.facts()),
        "metrics_pct_base_plus_free": metrics,
        "deep_fundamental_coverage": deep_coverage,
        "source_status": source_status,
        "results": results,
        "manual_import_root": str(IMPORT_ROOT),
        "yfinance_called_by_v211": False,
        "paid_sources_called": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (store.root / "V21.1_FREE_CAPTURE_AUDIT.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("V21_1_FREE_CAPTURE_OK", json.dumps({
        "rows": audit["rows"],
        "cache_identity_isin": audit["cache_identity_isin"],
        "cache_lei_isin": audit["cache_lei_isin"],
        "cache_market_isin": audit["cache_market_isin"],
        "cache_market_rows": audit["cache_market_rows"],
        "cache_fact_rows": audit["cache_fact_rows"],
        "metrics_pct_base_plus_free": audit["metrics_pct_base_plus_free"],
        "deep_fundamental_coverage": {
            "base_mean": deep_coverage["base_mean_field_coverage_pct"],
            "merged_mean": deep_coverage["merged_mean_field_coverage_pct"],
            "gain_points": deep_coverage["gain_points"],
            "schema_v2_completed": deep_coverage["esef_schema_v2_completed_isin"],
            "schema_v2_remaining": deep_coverage["esef_schema_v2_remaining_positive_isin"],
        },
    }))


if __name__ == "__main__":
    main()
