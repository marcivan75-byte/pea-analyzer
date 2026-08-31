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


def _compute_gates(report: dict[str, object]) -> dict[str, bool]:
    """Compute independent fail-closed authorization gates.

    The V22.1 historical backtest currently executed by this workflow is the
    technical-core model: it consumes only governed PIT price/volume-derived
    features.  Missing historical sector/fundamental observations must block a
    *full-process* validation, but must not be misreported as a blocker for a
    price/volume-only technical validation that does not consume those fields.
    """
    return {
        "ohlcv_coverage_ge_90pct": float(report.get("ohlcv_ticker_coverage", 0.0)) >= 0.90,
        "technical_pit_isin_coverage_ge_90pct": float(report.get("technical_pit_isin_coverage", 0.0)) >= 0.90,
        "sector_history_validated": bool(report.get("sector_history", False)),
        "quality_roe_debt_history_validated": bool(report.get("quality_roe_debt_history", False)),
    }


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

    gates = _compute_gates(report)
    technical_authorized = gates["ohlcv_coverage_ge_90pct"] and gates["technical_pit_isin_coverage_ge_90pct"]
    full_authorized = technical_authorized and gates["sector_history_validated"] and gates["quality_roe_debt_history_validated"]

    # Keep the legacy full-process field fail-closed.  Add a distinct field for
    # the technical-core scope so downstream jobs cannot confuse the two.
    report["technical_performance_validation_authorized"] = technical_authorized
    report["final_performance_validation_authorized"] = full_authorized
    report["full_process_performance_validation_authorized"] = full_authorized
    report["status"] = "READY_FULL_PIT" if full_authorized else "READY_TECHNICAL_ONLY" if technical_authorized else "BLOCKED"
    report["validation_scopes"] = {
        "technical_core_price_volume": {
            "authorized": technical_authorized,
            "requires": ["ohlcv_coverage_ge_90pct", "technical_pit_isin_coverage_ge_90pct"],
            "forbids_nonpit_substitution": True,
        },
        "full_process_including_nonprice": {
            "authorized": full_authorized,
            "requires": [
                "ohlcv_coverage_ge_90pct",
                "technical_pit_isin_coverage_ge_90pct",
                "sector_history_validated",
                "quality_roe_debt_history_validated",
            ],
            "forbids_nonpit_substitution": True,
        },
    }
    report["performance_gate_reasons"] = gates
    report.setdefault("governance", {})["protocol_files_never_count_as_historical_data"] = True
    report["governance"]["nonprice_evidence_content_validated"] = True
    report["governance"]["technical_scope_does_not_consume_sector_or_quality"] = True
    report["governance"]["full_process_stays_blocked_without_certified_nonprice_pit"] = True

    readiness_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
