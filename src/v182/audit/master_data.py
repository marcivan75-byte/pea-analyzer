from __future__ import annotations

from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

from v182.audit.canonical_universe import filter_actions
from v182.io.frames import is_missing, load_master

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
IDENTITY_ONLY_STATUS = "WHITELIST_ONLY_MISSING_METADATA"
SEVERITY_ORDER = {"INFO": 0, "WARN": 1, "BLOCK_DATA": 2, "FATAL": 3}

IDENTITY_FIELDS = {
    "ACTION": ("isin", "name", "yahoo_ticker"),
    "ETF": ("isin", "name", "yahoo_ticker", "primary_mic", "trading_currency"),
}
DATE_FIELDS = (
    "as_of_date", "enrichment_as_of", "mapping_as_of", "isin_validation_as_of",
    "ticker_validation_as_of", "ticker_validated_as_of", "referential_actualised_as_of",
    "perf_as_of", "fundamentals_as_of", "yf_consensus_as_of", "ta_as_of", "ohlcv_last",
)
NUMERIC_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "dividend_yield_pct": (0.0, 100.0),
    "ter_pct": (0.0, 10.0),
    "aum_m": (0.0, None),
    "fund_total_assets_eur_m": (0.0, None),
    "holdings": (0.0, None),
    "market_cap": (0.0, None),
    "volatility_1y_pct": (0.0, 500.0),
    "risk_indicator": (1.0, 7.0),
    "morningstar_rating": (1.0, 5.0),
    "rank_cat_1y": (0.0, 100.0),
    "rank_cat_3y": (0.0, 100.0),
    "rank_cat_5y": (0.0, 100.0),
}


@dataclass(frozen=True)
class MasterAuditResult:
    summary: dict[str, Any]
    issues: pd.DataFrame


def normalize_isin(value: Any) -> str:
    if is_missing(value):
        return ""
    return re.sub(r"\s+", "", str(value)).upper()


def isin_checksum_valid(value: Any) -> bool:
    """Validate an ISO-6166 style ISIN with the mod-10 checksum.

    This validates syntax/check digit only. It does not claim that the security
    exists or is PEA eligible; those require attributed reference evidence.
    """
    isin = normalize_isin(value)
    if not ISIN_RE.fullmatch(isin):
        return False
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in isin)
    total = 0
    for idx, ch in enumerate(reversed(digits)):
        n = int(ch)
        if idx % 2 == 1:
            n *= 2
        total += n // 10 + n % 10
    return total % 10 == 0


def _issue(issues: list[dict[str, Any]], universe: str, row: int | None, isin: str, field: str,
           code: str, severity: str, value: Any = "", detail: str = "") -> None:
    issues.append({
        "universe": universe,
        "row": row,
        "isin": isin,
        "field": field,
        "code": code,
        "severity": severity,
        "value": "" if is_missing(value) else str(value),
        "detail": detail,
    })


def _parse_number(value: Any) -> float | None:
    if is_missing(value):
        return None
    text = str(value).strip().replace("\u202f", "").replace(" ", "").replace(",", ".")
    try:
        result = float(text)
    except ValueError:
        return None
    return result if pd.notna(result) and result not in (float("inf"), float("-inf")) else None


def _audit_dates(frame: pd.DataFrame, universe: str, issues: list[dict[str, Any]], today: date) -> None:
    for field in DATE_FIELDS:
        if field not in frame.columns:
            continue
        for idx, value in frame[field].items():
            if is_missing(value):
                continue
            parsed = pd.to_datetime(value, errors="coerce", utc=True)
            isin = normalize_isin(frame.at[idx, "isin"]) if "isin" in frame.columns else ""
            if pd.isna(parsed):
                _issue(issues, universe, int(idx), isin, field, "DATE_UNPARSABLE", "WARN", value)
                continue
            if parsed.date() > today:
                _issue(issues, universe, int(idx), isin, field, "DATE_IN_FUTURE", "FATAL", value,
                       "A current-snapshot master must not contain future evidence timestamps.")


def _audit_numeric(frame: pd.DataFrame, universe: str, issues: list[dict[str, Any]]) -> None:
    bounds = dict(NUMERIC_BOUNDS)
    for field in frame.columns:
        if field.startswith("perf_") and field.endswith("_pct"):
            bounds.setdefault(field, (-100.0, 100000.0))
        if field in {"max_drawdown_1y_pct", "max_drawdown_1y"}:
            bounds.setdefault(field, (-100.0, 0.0))
    for field, (low, high) in bounds.items():
        if field not in frame.columns:
            continue
        for idx, value in frame[field].items():
            if is_missing(value):
                continue
            num = _parse_number(value)
            isin = normalize_isin(frame.at[idx, "isin"]) if "isin" in frame.columns else ""
            if num is None:
                _issue(issues, universe, int(idx), isin, field, "NUMERIC_UNPARSABLE", "BLOCK_DATA", value)
                continue
            if low is not None and num < low:
                _issue(issues, universe, int(idx), isin, field, "NUMERIC_BELOW_BOUND", "BLOCK_DATA", value,
                       f"expected >= {low}")
            if high is not None and num > high:
                _issue(issues, universe, int(idx), isin, field, "NUMERIC_ABOVE_BOUND", "BLOCK_DATA", value,
                       f"expected <= {high}")


def _audit_identity(frame: pd.DataFrame, universe: str, issues: list[dict[str, Any]]) -> None:
    if "isin" not in frame.columns:
        _issue(issues, universe, None, "", "isin", "ISIN_COLUMN_MISSING", "FATAL")
        return

    normalized = frame["isin"].map(normalize_isin)
    duplicate_mask = normalized.ne("") & normalized.duplicated(keep=False)
    for idx in frame.index[duplicate_mask]:
        _issue(issues, universe, int(idx), normalized.at[idx], "isin", "DUPLICATE_ISIN", "FATAL", frame.at[idx, "isin"])

    for idx, raw in frame["isin"].items():
        isin = normalized.at[idx]
        if not isin:
            _issue(issues, universe, int(idx), "", "isin", "ISIN_MISSING", "FATAL")
            continue
        if str(raw).strip() != isin:
            _issue(issues, universe, int(idx), isin, "isin", "ISIN_NOT_CANONICAL_FORMAT", "WARN", raw,
                   "Safe normalization is uppercase + whitespace removal; the source value should be corrected at ingestion.")
        if not isin_checksum_valid(isin):
            _issue(issues, universe, int(idx), isin, "isin", "ISIN_CHECKSUM_INVALID", "FATAL", raw)

        if "isin_checksum_after" in frame.columns and not is_missing(frame.at[idx, "isin_checksum_after"]):
            stated = str(frame.at[idx, "isin_checksum_after"]).strip().upper()
            actual = "VALID" if isin_checksum_valid(isin) else "INVALID"
            if stated != actual:
                _issue(issues, universe, int(idx), isin, "isin_checksum_after", "ISIN_STATUS_CONTRADICTION", "FATAL", stated,
                       f"computed={actual}")

        if "isin_correction_status" in frame.columns:
            correction = "" if is_missing(frame.at[idx, "isin_correction_status"]) else str(frame.at[idx, "isin_correction_status"]).upper()
            if correction.startswith("CORRECTED"):
                original = frame.at[idx, "original_isin"] if "original_isin" in frame.columns else ""
                source = frame.at[idx, "isin_validation_source"] if "isin_validation_source" in frame.columns else ""
                as_of = frame.at[idx, "isin_validation_as_of"] if "isin_validation_as_of" in frame.columns else ""
                if normalize_isin(original) == isin or is_missing(original) or is_missing(source) or is_missing(as_of):
                    _issue(issues, universe, int(idx), isin, "isin_correction_status", "ISIN_CORRECTION_PROVENANCE_INCOMPLETE",
                           "FATAL", correction, "Official correction requires different original ISIN, source and validation date.")

    expected_asset = "ACTION" if universe == "ACTION" else "ETF"
    if "asset_class" in frame.columns:
        for idx, value in frame["asset_class"].items():
            if is_missing(value):
                if universe == "ETF":
                    _issue(issues, universe, int(idx), normalized.at[idx], "asset_class", "ASSET_CLASS_MISSING", "BLOCK_DATA")
                continue
            if str(value).strip().upper() != expected_asset:
                _issue(issues, universe, int(idx), normalized.at[idx], "asset_class", "ASSET_CLASS_MISMATCH", "FATAL", value,
                       f"expected={expected_asset}")

    for field in IDENTITY_FIELDS[universe]:
        if field not in frame.columns:
            _issue(issues, universe, None, "", field, "IDENTITY_COLUMN_MISSING", "FATAL")
            continue
        for idx, value in frame[field].items():
            if not is_missing(value):
                continue
            isin = normalized.at[idx]
            identity_only = universe == "ACTION" and "canonical_seed_status" in frame.columns and str(frame.at[idx, "canonical_seed_status"]) == IDENTITY_ONLY_STATUS
            severity = "BLOCK_DATA" if identity_only or field != "isin" else "FATAL"
            _issue(issues, universe, int(idx), isin, field, "CRITICAL_IDENTITY_MISSING", severity, value,
                   "Identity-only canonical rows remain excluded from scoring until attributed hydration." if identity_only else "")

    if universe == "ETF" and "referential_status" in frame.columns:
        for idx, status in frame["referential_status"].items():
            if is_missing(status) or "FINAL_VALIDATED" not in str(status).upper():
                continue
            isin = normalized.at[idx]
            for field in ("yahoo_ticker", "ticker_primary", "primary_mic", "trading_currency"):
                if field in frame.columns and is_missing(frame.at[idx, field]):
                    _issue(issues, universe, int(idx), isin, field, "FINAL_STATUS_WITH_MISSING_IDENTITY", "FATAL", "",
                           f"referential_status={status}")
            source = frame.at[idx, "ticker_source_url_final"] if "ticker_source_url_final" in frame.columns else ""
            validated = frame.at[idx, "ticker_validated_as_of"] if "ticker_validated_as_of" in frame.columns else ""
            if is_missing(source) or is_missing(validated):
                _issue(issues, universe, int(idx), isin, "referential_status", "FINAL_STATUS_WITHOUT_PROVENANCE", "FATAL", status)


def _coverage(frame: pd.DataFrame, fields: tuple[str, ...]) -> dict[str, float]:
    result: dict[str, float] = {}
    for field in fields:
        if field not in frame.columns:
            result[field] = 0.0
        else:
            result[field] = round((~frame[field].map(is_missing)).mean() * 100.0, 2)
    return result


def audit_frame(frame: pd.DataFrame, universe: str, *, today: date | None = None) -> MasterAuditResult:
    universe = universe.upper()
    if universe not in {"ACTION", "ETF"}:
        raise ValueError(f"UNSUPPORTED_UNIVERSE:{universe}")
    frame = frame.reset_index(drop=True).copy()
    issues: list[dict[str, Any]] = []
    _audit_identity(frame, universe, issues)
    _audit_numeric(frame, universe, issues)
    _audit_dates(frame, universe, issues, today or datetime.now(timezone.utc).date())

    issue_frame = pd.DataFrame(issues, columns=("universe", "row", "isin", "field", "code", "severity", "value", "detail"))
    severity_counts = {key: 0 for key in SEVERITY_ORDER}
    if not issue_frame.empty:
        severity_counts.update({str(k): int(v) for k, v in issue_frame["severity"].value_counts().items()})
    normalized = frame["isin"].map(normalize_isin) if "isin" in frame.columns else pd.Series(dtype=str)
    valid_isin = int(normalized.map(isin_checksum_valid).sum()) if len(normalized) else 0
    identity_only = 0
    if universe == "ACTION" and "canonical_seed_status" in frame.columns:
        identity_only = int(frame["canonical_seed_status"].astype(str).eq(IDENTITY_ONLY_STATUS).sum())
    summary = {
        "universe": universe,
        "rows": int(len(frame)),
        "unique_isin": int(normalized[normalized.ne("")].nunique()) if len(normalized) else 0,
        "valid_isin_checksum": valid_isin,
        "invalid_or_missing_isin": int(len(frame) - valid_isin),
        "identity_only_rows": identity_only,
        "market_data_eligible_rows": int(len(frame) - identity_only),
        "identity_coverage_pct": _coverage(frame, IDENTITY_FIELDS[universe]),
        "issue_counts": severity_counts,
    }
    return MasterAuditResult(summary=summary, issues=issue_frame)


def run(root: Path, *, fail_fatal: bool = False) -> dict[str, Any]:
    actions_legacy = load_master(root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    canonical = filter_actions(actions_legacy, root / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts")
    etf = load_master(root / "inputs" / "V18.2_PEA_ETF_MASTER.csv")

    action_result = audit_frame(canonical.included, "ACTION")
    etf_result = audit_frame(etf, "ETF")
    issues = pd.concat([action_result.issues, etf_result.issues], ignore_index=True)
    fatal_count = int((issues["severity"] == "FATAL").sum()) if not issues.empty else 0
    block_count = int((issues["severity"] == "BLOCK_DATA").sum()) if not issues.empty else 0

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract": "CURRENT_SNAPSHOT_IDENTITY_AND_VALUE_INTEGRITY; dynamic historical backtests require PIT observations, never the current snapshot as historical truth.",
        "actions": action_result.summary,
        "etf": etf_result.summary,
        "legacy_action_rows": int(len(actions_legacy)),
        "excluded_legacy_action_rows": int(len(canonical.excluded)),
        "canonical_materialized_missing_actions": int(canonical.materialized_missing_count),
        "fatal_count": fatal_count,
        "block_data_count": block_count,
        "passed_structural_integrity": fatal_count == 0,
        "scoring_policy": "Rows with BLOCK_DATA issues remain non-scoring for affected data; missing values are never imputed as neutral.",
        "backtest_policy": "Current master dynamic fields are not PIT evidence. Historical modules must use timestamped observations available at the simulated decision time.",
    }

    outdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "MASTER_DATA_AUDIT.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    issues.to_csv(outdir / "MASTER_DATA_AUDIT_ISSUES.csv", sep=";", encoding="utf-8-sig", index=False)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if fail_fatal and fatal_count:
        raise SystemExit(f"MASTER_DATA_FATAL_ISSUES:{fatal_count}")
    return payload


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--fail-fatal", action="store_true")
    args = parser.parse_args()
    run(Path(args.root).resolve(), fail_fatal=args.fail_fatal)


if __name__ == "__main__":
    main()
