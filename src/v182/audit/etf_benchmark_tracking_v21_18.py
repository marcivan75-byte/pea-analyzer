from __future__ import annotations

from collections import Counter
from pathlib import Path
import json

import pandas as pd

from v182.io.frames import is_missing

ROOT = Path(__file__).resolve().parents[3]
TRACKING_FIELDS = ("tracking_error_1y_pct", "tracking_error_3y_pct", "tracking_error_5y_pct")
PRICE_MAP_COLUMNS = (
    "official_benchmark",
    "benchmark_price_symbol",
    "provider",
    "source",
    "source_url",
    "evidence_level",
    "validated_as_of",
    "status",
)


def _clean(value: object) -> str:
    if is_missing(value):
        return ""
    return str(value).strip()


def load_config(path: str | Path | None = None) -> dict:
    resolved = Path(path) if path is not None else ROOT / "config" / "ETF_BENCHMARK_TRACKING_V21_18.json"
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    governance = payload.get("governance") or {}
    required_false = (
        "benchmark_name_inference",
        "benchmark_price_symbol_inference",
        "tracking_error_computation_enabled",
        "live_orders_enabled",
        "weights_changed",
        "thresholds_changed",
        "t1_t2_scope_changed",
        "holdout_opened",
    )
    if any(governance.get(key) is not False for key in required_false):
        raise ValueError("ETF_BENCHMARK_TRACKING_GOVERNANCE_DRIFT")
    if governance.get("decision_influence") != 0.0:
        raise ValueError("ETF_BENCHMARK_TRACKING_DECISION_INFLUENCE_FORBIDDEN")
    if governance.get("exact_benchmark_name_match_required") is not True:
        raise ValueError("ETF_BENCHMARK_TRACKING_EXACT_NAME_MATCH_REQUIRED")
    return payload


def load_price_map(path: str | Path, cfg: dict) -> tuple[dict[str, dict], dict]:
    resolved = Path(path)
    if not resolved.exists() or resolved.stat().st_size == 0:
        return {}, {"status": "NO_PRICE_MAP", "rows": 0, "verified_rows": 0}
    frame = pd.read_csv(resolved, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
    missing = set(PRICE_MAP_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"ETF_BENCHMARK_PRICE_MAP_MISSING_COLUMNS:{','.join(sorted(missing))}")
    names = frame["official_benchmark"].astype(str).str.strip()
    if names.replace("", pd.NA).dropna().duplicated().any():
        raise ValueError("ETF_BENCHMARK_PRICE_MAP_DUPLICATE_EXACT_NAME")
    accepted_status = set(map(str, cfg["accepted_price_mapping_statuses"]))
    accepted_evidence = set(map(str, cfg["accepted_price_mapping_evidence"]))
    verified: dict[str, dict] = {}
    rejected: Counter[str] = Counter()
    for _, row in frame.iterrows():
        name = _clean(row.get("official_benchmark"))
        symbol = _clean(row.get("benchmark_price_symbol"))
        status = _clean(row.get("status"))
        evidence = _clean(row.get("evidence_level")).upper()
        source_url = _clean(row.get("source_url"))
        validated = pd.to_datetime(row.get("validated_as_of"), errors="coerce", utc=True)
        if not name:
            rejected["MISSING_BENCHMARK_NAME"] += 1
            continue
        if not symbol:
            rejected["MISSING_PRICE_SYMBOL"] += 1
            continue
        if status not in accepted_status:
            rejected["STATUS_REJECTED"] += 1
            continue
        if evidence not in accepted_evidence:
            rejected["EVIDENCE_REJECTED"] += 1
            continue
        if not source_url:
            rejected["MISSING_SOURCE_URL"] += 1
            continue
        if pd.isna(validated):
            rejected["VALIDATED_AS_OF_INVALID"] += 1
            continue
        verified[name] = {key: _clean(row.get(key)) for key in PRICE_MAP_COLUMNS}
    return verified, {
        "status": "SUCCESS",
        "rows": int(len(frame)),
        "verified_rows": int(len(verified)),
        "rejected": {key: int(value) for key, value in sorted(rejected.items())},
        "exact_name_match": True,
    }


def classify_master(master: pd.DataFrame, price_map: dict[str, dict], cfg: dict) -> tuple[pd.DataFrame, dict]:
    if "isin" not in master.columns:
        raise ValueError("ETF_BENCHMARK_TRACKING_MASTER_ISIN_REQUIRED")
    rows: list[dict] = []
    for _, item in master.iterrows():
        isin = _clean(item.get("isin"))
        benchmark = _clean(item.get("official_benchmark"))
        vendor_tracking = {
            field: None if is_missing(item.get(field)) else item.get(field)
            for field in TRACKING_FIELDS
        }
        vendor_present = [field for field, value in vendor_tracking.items() if value is not None]
        mapping = price_map.get(benchmark) if benchmark else None
        if not benchmark:
            readiness = "BENCHMARK_NAME_MISSING"
        elif mapping is None:
            readiness = "BENCHMARK_PRICE_MAPPING_BLOCKED"
        else:
            readiness = "BENCHMARK_PRICE_MAPPING_VERIFIED_RESEARCH_ONLY"
        rows.append({
            "isin": isin,
            "name": _clean(item.get("name")),
            "official_benchmark": benchmark,
            "benchmark_price_symbol": "" if mapping is None else mapping["benchmark_price_symbol"],
            "benchmark_price_provider": "" if mapping is None else mapping["provider"],
            "benchmark_mapping_evidence": "" if mapping is None else mapping["evidence_level"],
            "vendor_tracking_fields_present": "|".join(vendor_present),
            "vendor_tracking_field_count": len(vendor_present),
            "tracking_readiness": readiness,
            "tracking_error_computation_enabled": False,
            "tracking_error_decision_influence": 0.0,
        })
    result = pd.DataFrame(rows)
    statuses = Counter(result["tracking_readiness"].astype(str)) if not result.empty else Counter()
    benchmark_count = int(result["official_benchmark"].astype(str).str.strip().ne("").sum()) if not result.empty else 0
    vendor_any = int(pd.to_numeric(result["vendor_tracking_field_count"], errors="coerce").gt(0).sum()) if not result.empty else 0
    mapped = int(result["tracking_readiness"].eq("BENCHMARK_PRICE_MAPPING_VERIFIED_RESEARCH_ONLY").sum()) if not result.empty else 0
    summary = {
        "version": cfg["version"],
        "mode": cfg["mode"],
        "status": "SUCCESS",
        "etf_rows": int(len(result)),
        "official_benchmark_rows": benchmark_count,
        "official_benchmark_coverage_pct": round(100.0 * benchmark_count / len(result), 2) if len(result) else 0.0,
        "vendor_tracking_any_rows": vendor_any,
        "verified_benchmark_price_mapping_rows": mapped,
        "tracking_readiness_counts": {key: int(value) for key, value in sorted(statuses.items())},
        "tracking_error_computation_enabled": False,
        "decision_influence": 0.0,
        "live_orders_enabled": False,
        "governance": cfg["governance"],
    }
    return result, summary


def run(root: Path = ROOT) -> dict:
    cfg = load_config(root / "config" / "ETF_BENCHMARK_TRACKING_V21_18.json")
    master_path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not master_path.exists():
        master_path = root / "inputs" / "V18.2_PEA_ETF_MASTER.csv"
    master = pd.read_csv(master_path, sep=";", encoding="utf-8-sig", low_memory=False)
    price_map_path = root / str(cfg["benchmark_price_map_path"])
    price_map, map_diag = load_price_map(price_map_path, cfg)
    rows, summary = classify_master(master, price_map, cfg)
    summary["source_master"] = str(master_path.relative_to(root))
    summary["price_map"] = map_diag
    outputs = root / "outputs" / "audit"
    outputs.mkdir(parents=True, exist_ok=True)
    rows.to_csv(outputs / "V21_18_ETF_BENCHMARK_TRACKING_READINESS.csv", sep=";", index=False, encoding="utf-8-sig")
    (outputs / "V21_18_ETF_BENCHMARK_TRACKING_READINESS.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()
