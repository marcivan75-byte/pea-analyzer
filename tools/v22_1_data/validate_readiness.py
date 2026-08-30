from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

READINESS = Path("outputs/hebdo/data_v22_1/V22_1_DATA_READINESS.json")
MIN_ROWS = 10


def _columns_and_rows(path: Path) -> tuple[set[str], int]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path)
        return {str(c).lower() for c in df.columns}, int(len(df))
    if suffix == ".parquet":
        df = pd.read_parquet(path)
        return {str(c).lower() for c in df.columns}, int(len(df))
    if suffix == ".jsonl":
        df = pd.read_json(path, lines=True)
        return {str(c).lower() for c in df.columns}, int(len(df))
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            df = pd.DataFrame(payload)
        elif isinstance(payload, dict):
            rows = next((payload[k] for k in ("rows", "data", "records") if isinstance(payload.get(k), list)), None)
            if rows is None:
                return set(), 0
            df = pd.DataFrame(rows)
        else:
            return set(), 0
        return {str(c).lower() for c in df.columns}, int(len(df))
    return set(), 0


def _looks_historical(cols: set[str]) -> bool:
    return bool(cols.intersection({"date", "as_of_date", "asof_date", "signal_date", "publication_date", "period_end", "fiscal_period_end"}))


def _validate_evidence(root: Path, rel: str, kind: str) -> tuple[bool, str]:
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return False, "outside_repository"
    if not path.exists() or not path.is_file():
        return False, "missing"
    # Configuration/protocol files define rules; they are never historical observations.
    if "config" in path.parts or "protocol" in path.name.lower() or "schema" in path.name.lower():
        return False, "protocol_or_config_not_data"
    try:
        cols, rows = _columns_and_rows(path)
    except Exception as exc:
        return False, f"unreadable:{type(exc).__name__}"
    if rows < MIN_ROWS:
        return False, f"too_few_rows:{rows}"
    if not _looks_historical(cols):
        return False, "missing_historical_date_column"
    if kind == "sector" and not cols.intersection({"sector", "sector_name", "sector_id", "industry", "industry_group"}):
        return False, "missing_sector_column"
    if kind == "quality" and not cols.intersection({"roe", "return_on_equity", "debt", "net_debt", "debt_to_equity", "debt_ebitda", "quality_score"}):
        return False, "missing_quality_column"
    return True, "validated_dataset"


def main() -> int:
    root = Path(".").resolve()
    readiness_path = root / READINESS
    report = json.loads(readiness_path.read_text(encoding="utf-8"))
    evidence = [str(x) for x in report.get("historical_nonprice_evidence", [])]

    validated_sector: list[str] = []
    validated_quality: list[str] = []
    rejected: list[dict[str, str]] = []

    for rel in evidence:
        low = Path(rel).name.lower()
        candidate_kinds = []
        if "sector" in low:
            candidate_kinds.append("sector")
        if any(k in low for k in ("fundamental", "financial", "quality", "roe", "debt")):
            candidate_kinds.append("quality")
        if not candidate_kinds:
            rejected.append({"path": rel, "reason": "unclassified_nonprice_evidence"})
            continue
        any_valid = False
        for kind in candidate_kinds:
            ok, reason = _validate_evidence(root, rel, kind)
            if ok:
                any_valid = True
                (validated_sector if kind == "sector" else validated_quality).append(rel)
            else:
                rejected.append({"path": rel, "kind": kind, "reason": reason})
        if not any_valid:
            continue

    report["sector_history"] = bool(validated_sector)
    report["quality_roe_debt_history"] = bool(validated_quality)
    report["historical_nonprice_evidence_validated"] = sorted(set(validated_sector + validated_quality))
    report["historical_nonprice_evidence_rejected"] = rejected

    price_gate = float(report.get("ohlcv_ticker_coverage", 0.0)) >= 0.90
    pit_gate = float(report.get("technical_pit_isin_coverage", 0.0)) >= 0.90
    sector_gate = bool(report["sector_history"])
    quality_gate = bool(report["quality_roe_debt_history"])
    authorized = price_gate and pit_gate and sector_gate and quality_gate
    report["final_performance_validation_authorized"] = authorized
    report["status"] = "READY_FULL_PIT" if authorized else "READY_TECHNICAL_ONLY"
    report["performance_gate_reasons"] = {
        "ohlcv_coverage_ge_90pct": price_gate,
        "technical_pit_isin_coverage_ge_90pct": pit_gate,
        "sector_history_validated": sector_gate,
        "quality_roe_debt_history_validated": quality_gate,
    }
    report.setdefault("governance", {})["protocol_files_never_count_as_historical_data"] = True
    report["governance"]["nonprice_evidence_content_validated"] = True

    readiness_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
