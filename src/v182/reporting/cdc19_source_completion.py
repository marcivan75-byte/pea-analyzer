from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

import pandas as pd

from v182.io.frames import is_missing
from v182.sources.cdc19_macro_market import (
    fetch_ecb_rates,
    fetch_eia_energy,
    fetch_eurostat_hicp,
    fetch_marketstack_latest,
)
from v182.sources.cdc19_sentiment import (
    fetch_aaii_sentiment,
    fetch_cnn_fear_greed,
    fetch_google_news_context,
)
from v182.sources.cdc19_snapshots import load_all_snapshots

MATRIX_PATH = "config/V21_CDC19_SOURCE_MATRIX.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _obs(universe: str, isin: str, field: str, value, source: str, evidence: str, as_of: str | None = None) -> dict:
    return {
        "universe": universe,
        "isin": isin,
        "field": field,
        "value": value,
        "source": source,
        "collected_at": _now(),
        "as_of": as_of or datetime.now(timezone.utc).date().isoformat(),
        "evidence_level": evidence,
        "validation_status": "AUTO_MATCH",
    }


def _broadcast_global(actions: pd.DataFrame, etfs: pd.DataFrame, fields: dict, source: str, evidence: str) -> list[dict]:
    if not fields:
        return []
    out = []
    for universe, frame in (("ACTION", actions), ("ETF", etfs)):
        if "isin" not in frame.columns:
            continue
        for isin in frame["isin"].astype(str).str.strip():
            if not isin:
                continue
            for field, value in fields.items():
                if value is None:
                    continue
                out.append(_obs(universe, isin, field, value, source, evidence))
    return out


def _marketstack_scope(actions: pd.DataFrame, etfs: pd.DataFrame) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """Return only explicitly mapped Marketstack symbols for missing prices.

    A Yahoo suffix is not stripped or guessed because a base symbol can refer to
    multiple securities across exchanges.
    """
    mapping: dict[str, tuple[str, str]] = {}
    for universe, frame in (("ACTION", actions), ("ETF", etfs)):
        if frame.empty or "isin" not in frame.columns or "marketstack_symbol" not in frame.columns:
            continue
        last_close = frame["last_close"] if "last_close" in frame.columns else pd.Series(pd.NA, index=frame.index)
        for idx, row in frame.iterrows():
            if not is_missing(last_close.loc[idx]):
                continue
            symbol = str(row.get("marketstack_symbol") or "").strip()
            isin = str(row.get("isin") or "").strip()
            if symbol and isin:
                mapping[symbol] = (universe, isin)
    return sorted(mapping), mapping


def _marketstack_observations(actions: pd.DataFrame, etfs: pd.DataFrame, api_key: str | None, cfg: dict) -> tuple[list[dict], list[dict], dict]:
    symbols, mapping = _marketstack_scope(actions, etfs)
    spec = cfg.get("cdc19_sources", {}).get("marketstack", {})
    raw, failures, stats = fetch_marketstack_latest(
        symbols,
        api_key,
        batch_size=int(spec.get("batch_size", 50)),
        max_requests=int(spec.get("max_requests_per_run", 2)),
    )
    observations = []
    for row in raw:
        symbol = str(row.get("ticker") or "").strip()
        target = mapping.get(symbol)
        if target is None:
            failures.append({"source": "Marketstack", "ticker": symbol, "reason": "UNMAPPED_RESPONSE_SYMBOL"})
            continue
        universe, isin = target
        for raw_field, field in (("close", "last_close"), ("open", "open"), ("high", "high"), ("low", "low"), ("volume", "volume")):
            value = row.get(raw_field)
            if value is not None:
                observations.append(_obs(universe, isin, field, value, "Marketstack", "C", row.get("date")))
    stats["mapped_symbols"] = len(mapping)
    stats["observations"] = len(observations)
    return observations, failures, stats


def _load_matrix(root: Path) -> dict:
    path = root / MATRIX_PATH
    if not path.exists():
        raise FileNotFoundError(f"Missing CDC19 source matrix: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def collect(actions: pd.DataFrame, etfs: pd.DataFrame, cfg: dict, root: Path) -> tuple[list[dict], list[dict], dict]:
    matrix = _load_matrix(root)
    observations: list[dict] = []
    failures: list[dict] = []
    runtime: dict[str, dict] = {}
    global_context: dict = {}

    ecb_fields, ecb_fail = fetch_ecb_rates()
    observations.extend(_broadcast_global(actions, etfs, ecb_fields, "ECB", "A"))
    failures.extend(ecb_fail)
    global_context.update(ecb_fields)
    runtime["ECB"] = {"observed_fields": len(ecb_fields), "failures": len(ecb_fail)}

    eurostat_fields, eurostat_fail = fetch_eurostat_hicp()
    observations.extend(_broadcast_global(actions, etfs, eurostat_fields, "Eurostat", "A"))
    failures.extend(eurostat_fail)
    global_context.update(eurostat_fields)
    runtime["Eurostat"] = {"observed_fields": len(eurostat_fields), "failures": len(eurostat_fail)}

    eia_fields, eia_fail = fetch_eia_energy(os.environ.get("EIA_API_KEY"))
    observations.extend(_broadcast_global(actions, etfs, eia_fields, "EIA", "A"))
    failures.extend(eia_fail)
    global_context.update(eia_fields)
    runtime["EIA"] = {"observed_fields": len(eia_fields), "failures": len(eia_fail)}

    google_fields, google_fail = fetch_google_news_context()
    observations.extend(_broadcast_global(actions, etfs, google_fields, "Google News", "C"))
    failures.extend(google_fail)
    global_context.update(google_fields)
    runtime["Google News"] = {"observed_fields": len(google_fields), "failures": len(google_fail)}

    cnn_fields, cnn_fail = fetch_cnn_fear_greed()
    observations.extend(_broadcast_global(actions, etfs, cnn_fields, "CNN Fear & Greed", "C"))
    failures.extend(cnn_fail)
    global_context.update(cnn_fields)
    runtime["CNN Fear & Greed"] = {"observed_fields": len(cnn_fields), "failures": len(cnn_fail)}

    aaii_fields, aaii_fail = fetch_aaii_sentiment()
    observations.extend(_broadcast_global(actions, etfs, aaii_fields, "AAII", "B"))
    failures.extend(aaii_fail)
    global_context.update(aaii_fields)
    runtime["AAII"] = {"observed_fields": len(aaii_fields), "failures": len(aaii_fail)}

    market_obs, market_fail, market_stats = _marketstack_observations(actions, etfs, os.environ.get("MARKETSTACK_API_KEY"), cfg)
    observations.extend(market_obs)
    failures.extend(market_fail)
    runtime["Marketstack"] = market_stats | {"failures": len(market_fail)}

    snap_obs, snap_fail, snap_stats = load_all_snapshots(root, actions, etfs)
    observations.extend(snap_obs)
    failures.extend(snap_fail)
    runtime.update(snap_stats)

    # Sources already collected by dedicated upstream waves are represented in
    # the matrix but not called twice here (no-repeat API request policy).
    for source in ("Yahoo Finance", "Finnhub", "FRED", "OpenFIGI", "GDELT", "AMF Open Data", "Morningstar FR"):
        runtime.setdefault(source, {"mode": "UPSTREAM_DEDICATED_WAVE", "duplicate_request": False})
    runtime["NBER"] = {"mode": "METHODOLOGY_REFERENCE_ONLY", "runtime_data": False}

    outdir = root / "outputs" / "data_audit"
    outdir.mkdir(parents=True, exist_ok=True)
    context_payload = {
        "version": matrix.get("version", "CDC19"),
        "collected_at": _now(),
        "global_context": global_context,
        "runtime": runtime,
        "score_changes": False,
        "live_orders_enabled": False,
    }
    (outdir / "CDC19_GLOBAL_CONTEXT.json").write_text(json.dumps(context_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    rows = []
    for spec in matrix.get("sources", []):
        source = spec["source"]
        rt = runtime.get(source, {})
        rows.append({
            "cdc_index": spec["index"],
            "source": source,
            "role": spec.get("role", ""),
            "target_mode": spec.get("mode", ""),
            "implementation_status": spec.get("implementation_status", ""),
            "runtime_mode": rt.get("mode", "AUTOMATED_ONLINE" if source in runtime else "NOT_ATTEMPTED"),
            "runtime_observed_fields": rt.get("observed_fields", rt.get("observations", "")),
            "runtime_failures": rt.get("failures", ""),
            "runtime_data": False if source == "NBER" else True,
            "score_influence": 0,
            "governance_note": spec.get("governance_note", ""),
        })
    coverage = pd.DataFrame(rows).sort_values("cdc_index")
    coverage.to_csv(outdir / "CDC19_SOURCE_COVERAGE.csv", sep=";", index=False, encoding="utf-8-sig")

    summary = {
        "status": "SUCCESS_WITH_NONBLOCKING_SOURCE_GAPS" if failures else "SUCCESS",
        "framework_sources": int(len(coverage)),
        "runtime_data_channels": int((coverage["runtime_data"] == True).sum()),
        "methodology_references": int((coverage["runtime_data"] == False).sum()),
        "new_automated_sources_attempted": 7,
        "snapshot_sources_supported": 5,
        "observations": len(observations),
        "failures": len(failures),
        "score_changes": False,
        "t1_t2_score_influence": 0.0,
        "live_orders_enabled": False,
        "coverage_csv": "outputs/data_audit/CDC19_SOURCE_COVERAGE.csv",
        "global_context_json": "outputs/data_audit/CDC19_GLOBAL_CONTEXT.json",
    }
    (outdir / "CDC19_SOURCE_COMPLETION_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return observations, failures, summary
