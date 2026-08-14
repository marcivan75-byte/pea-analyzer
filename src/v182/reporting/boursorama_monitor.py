from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]

BOURSORAMA_ACTION_FIELDS = (
    "boursorama_consensus_signal",
    "boursorama_consensus_note_median",
    "boursorama_consensus_bucket",
    "boursorama_acheter_n",
    "boursorama_renforcer_n",
    "boursorama_conserver_n",
    "boursorama_alleger_n",
    "boursorama_vendre_n",
    "boursorama_analyst_count",
    "boursorama_target_price",
    "boursorama_target_upside_pct",
    "boursorama_per",
    "boursorama_dividend_yield_pct",
    "boursorama_52w_high",
    "boursorama_52w_low",
)


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    for sep in (";", ",", "\t"):
        try:
            frame = pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)
            if len(frame.columns) > 1:
                return frame
        except (OSError, UnicodeError, pd.errors.ParserError):
            continue
    return pd.DataFrame()


def _missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip().lower() in {"", "nan", "none", "null", "n/a", "na", "<na>"}


def _reason_counts(frame: pd.DataFrame, source: str) -> dict[str, int]:
    if frame.empty or "reason" not in frame.columns:
        return {}
    subset = frame
    if "source" in frame.columns:
        subset = frame.loc[frame["source"].astype(str).str.casefold() == source.casefold()]
    values = [str(v).strip() for v in subset["reason"].tolist() if not _missing(v)]
    return dict(sorted(Counter(values).items()))


def _count_http_blocks(reasons: dict[str, int]) -> int:
    return sum(count for reason, count in reasons.items() if reason in {"HTTP_403", "HTTP_429"})


def _count_transient_http(reasons: dict[str, int]) -> int:
    total = 0
    for reason, count in reasons.items():
        if reason.startswith("HTTP_5") or reason in {"HTTP_408", "HTTP_425", "HTTP_429"}:
            total += count
    return total


def _field_coverage(frame: pd.DataFrame, fields: tuple[str, ...]) -> dict[str, float]:
    if frame.empty:
        return {field: 0.0 for field in fields}
    out: dict[str, float] = {}
    for field in fields:
        if field not in frame.columns:
            out[field] = 0.0
            continue
        observed = (~frame[field].map(_missing)).sum()
        out[field] = round(float(observed) / max(len(frame), 1) * 100.0, 2)
    return out


def _available_rows(frame: pd.DataFrame, fields: tuple[str, ...]) -> int:
    if frame.empty:
        return 0
    present = pd.DataFrame(index=frame.index)
    for field in fields:
        present[field] = False if field not in frame.columns else ~frame[field].map(_missing)
    return int(present.any(axis=1).sum())


def run(root: Path = ROOT) -> dict:
    outputs = root / "outputs"
    etf = _read_csv(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    etf_failures = _read_csv(outputs / "gaps" / "V21_ETF_BOURSORAMA_RANK_FAILURES.csv")
    action = _read_csv(outputs / "committee_master" / "POSTSELECTION_MARKET_SHEETS.csv")
    action_failures = _read_csv(outputs / "gaps" / "V21_6_3_POSTSELECTION_MARKET_SHEETS_FAILURES.csv")

    etf_total = int(etf["isin"].astype(str).nunique()) if not etf.empty and "isin" in etf.columns else 0
    etf_success = 0
    if not etf.empty and "boursorama_category_rank_latest" in etf.columns:
        success_mask = ~etf["boursorama_category_rank_latest"].map(_missing)
        etf_success = int(etf.loc[success_mask, "isin"].astype(str).nunique()) if "isin" in etf.columns else int(success_mask.sum())
    etf_coverage = round(etf_success / etf_total * 100.0, 2) if etf_total else 0.0

    action_total = int(action["isin"].astype(str).nunique()) if not action.empty and "isin" in action.columns else 0
    action_boursorama_available = _available_rows(action, BOURSORAMA_ACTION_FIELDS)
    action_coverage = round(action_boursorama_available / action_total * 100.0, 2) if action_total else 0.0

    etf_reasons = _reason_counts(etf_failures, "Boursorama")
    action_reasons = _reason_counts(action_failures, "Boursorama")
    blocking_http = _count_http_blocks(etf_reasons) + _count_http_blocks(action_reasons)
    transient_http = _count_transient_http(etf_reasons) + _count_transient_http(action_reasons)
    ambiguous = sum(
        count
        for reasons in (etf_reasons, action_reasons)
        for reason, count in reasons.items()
        if "AMBIGU" in reason.upper() or "IDENTITY" in reason.upper()
    )

    status = "PASS"
    warnings: list[str] = []
    if blocking_http:
        status = "WARN"
        warnings.append(f"BOURSORAMA_HTTP_BLOCK_OR_RATE_LIMIT:{blocking_http}")
    if transient_http:
        status = "WARN"
        warnings.append(f"BOURSORAMA_TRANSIENT_HTTP:{transient_http}")
    if etf_total and etf_coverage < 50.0:
        status = "WARN"
        warnings.append(f"ETF_RANK_COVERAGE_LOW:{etf_coverage}%")
    if action_total and action_coverage < 50.0:
        status = "WARN"
        warnings.append(f"ACTION_POSTSELECTION_COVERAGE_LOW:{action_coverage}%")
    if ambiguous:
        status = "WARN"
        warnings.append(f"IDENTITY_AMBIGUITY_REJECTED:{ambiguous}")
    if not etf_total and not action_total:
        status = "NO_DATA"
        warnings.append("NO_BOURSORAMA_IMPORT_OUTPUTS")

    payload = {
        "version": "V21.6.3_BOURSORAMA_IMPORT_MONITOR",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "decision_influence": 0.0,
        "warnings": warnings,
        "etf": {
            "universe_rows": etf_total,
            "rank_success_isins": etf_success,
            "rank_coverage_pct": etf_coverage,
            "failure_reasons": etf_reasons,
        },
        "actions_postselection": {
            "shortlisted_isins": action_total,
            "boursorama_available_isins": action_boursorama_available,
            "boursorama_coverage_pct": action_coverage,
            "field_coverage_pct": _field_coverage(action, BOURSORAMA_ACTION_FIELDS),
            "failure_reasons": action_reasons,
        },
        "network": {
            "http_403_or_429": blocking_http,
            "transient_http": transient_http,
        },
        "identity": {
            "ambiguities_rejected": ambiguous,
            "policy": "AMBIGUOUS_MATCHES_REJECTED_NEVER_GUESSED",
        },
        "governance": {
            "missing_never_imputed": True,
            "http_200_without_fields_is_failure": True,
            "raw_etf_rank_no_percentile_fabrication": True,
            "postselection_score_mutation_forbidden": True,
            "postselection_decision_mutation_forbidden": True,
        },
    }

    audit_dir = outputs / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / "BOURSORAMA_IMPORT_MONITOR.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows: list[dict] = []
    for scope, reasons in (("ETF_RANK", etf_reasons), ("ACTION_POSTSELECTION", action_reasons)):
        for reason, count in reasons.items():
            rows.append({"scope": scope, "reason": reason, "count": int(count)})
    pd.DataFrame(rows, columns=["scope", "reason", "count"]).to_csv(
        audit_dir / "BOURSORAMA_IMPORT_FAILURE_REASON_COUNTS.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
