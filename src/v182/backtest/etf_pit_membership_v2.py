from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]

REQUIRED_COLUMNS = {
    "isin", "membership_start", "membership_end", "pea_eligibility_start",
    "pea_eligibility_end", "trading_existence_start", "trading_existence_end",
    "membership_source", "eligibility_source", "pit_status", "promotion_eligible",
}


def _date(value):
    if value is None or str(value).strip() == "":
        return pd.NaT
    return pd.to_datetime(value, errors="coerce").normalize()


def enrich_membership_from_quality(membership: pd.DataFrame, quality: pd.DataFrame) -> pd.DataFrame:
    """Add only evidence that can be derived safely from observed price history.

    First observed trading date proves existence no later than that date. It does NOT
    prove PEA eligibility or membership in the historical investable universe.
    """
    out = membership.copy()
    quality_cols = [c for c in ["isin", "first_date", "last_date", "rows", "mt_close_only_ready"] if c in quality.columns]
    q = quality[quality_cols].drop_duplicates("isin") if quality_cols else pd.DataFrame(columns=["isin"])
    out = out.merge(q, on="isin", how="left", suffixes=("", "_quality"))
    out["trading_existence_start"] = out.get("trading_existence_start", "")
    out["trading_existence_end"] = out.get("trading_existence_end", "")
    missing_start = out["trading_existence_start"].astype(str).str.strip().eq("")
    out.loc[missing_start, "trading_existence_start"] = out.loc[missing_start, "first_date"].fillna("")
    # last observed date is not a delisting date; keep it as evidence, not membership_end.
    out["last_observed_trading_date"] = out.get("last_date", "")
    out["existence_evidence_source"] = "OBSERVED_OHLCV_HISTORY"
    out["membership_source"] = out.get("membership_source", "CURRENT_MASTER_ONLY")
    out["eligibility_source"] = out.get("eligibility_source", "UNKNOWN")
    out["pit_status"] = out.get("pit_status", "UNKNOWN_RESEARCH_ONLY")
    out["promotion_eligible"] = False
    return out


def row_is_pit_eligible(row: pd.Series, as_of) -> bool:
    """Strict PIT gate. Unknown dates never default to eligible."""
    d = _date(as_of)
    if pd.isna(d):
        return False
    membership_start = _date(row.get("membership_start"))
    eligibility_start = _date(row.get("pea_eligibility_start"))
    existence_start = _date(row.get("trading_existence_start"))
    if pd.isna(membership_start) or pd.isna(eligibility_start) or pd.isna(existence_start):
        return False
    membership_end = _date(row.get("membership_end"))
    eligibility_end = _date(row.get("pea_eligibility_end"))
    existence_end = _date(row.get("trading_existence_end"))
    if d < max(membership_start, eligibility_start, existence_start):
        return False
    for end in (membership_end, eligibility_end, existence_end):
        if pd.notna(end) and d > end:
            return False
    return True


def validate_membership_table(frame: pd.DataFrame) -> dict:
    missing_cols = sorted(REQUIRED_COLUMNS - set(frame.columns))
    duplicate_isin = int(frame["isin"].duplicated().sum()) if "isin" in frame.columns else len(frame)
    complete_membership_start = int(frame["membership_start"].astype(str).str.strip().ne("").sum()) if "membership_start" in frame.columns else 0
    complete_eligibility_start = int(frame["pea_eligibility_start"].astype(str).str.strip().ne("").sum()) if "pea_eligibility_start" in frame.columns else 0
    complete_existence_start = int(frame["trading_existence_start"].astype(str).str.strip().ne("").sum()) if "trading_existence_start" in frame.columns else 0
    rows = len(frame)
    fully_documented = 0
    if not missing_cols and rows:
        fully_documented = int((
            frame["membership_start"].astype(str).str.strip().ne("") &
            frame["pea_eligibility_start"].astype(str).str.strip().ne("") &
            frame["trading_existence_start"].astype(str).str.strip().ne("") &
            frame["membership_source"].astype(str).str.strip().ne("") &
            frame["eligibility_source"].astype(str).str.strip().ne("")
        ).sum())
    return {
        "rows": rows,
        "missing_required_columns": missing_cols,
        "duplicate_isin": duplicate_isin,
        "membership_start_documented": complete_membership_start,
        "pea_eligibility_start_documented": complete_eligibility_start,
        "trading_existence_start_documented": complete_existence_start,
        "fully_documented_rows": fully_documented,
        "fully_documented_pct": 0.0 if rows == 0 else round(100.0 * fully_documented / rows, 4),
        "promotion_ready": bool(rows and not missing_cols and duplicate_isin == 0 and fully_documented == rows),
    }


def build(root: Path = ROOT) -> dict:
    base = root / "data" / "backtest" / "etf_base_v1"
    membership_path = base / "ETF_PIT_MEMBERSHIP.csv"
    quality_path = base / "ETF_BACKTEST_INSTRUMENT_QUALITY.csv"
    if not membership_path.exists() or not quality_path.exists():
        raise RuntimeError("ETF_PIT_V2_REQUIRES_BASE_V1_OUTPUTS")
    membership = pd.read_csv(membership_path, sep=";", dtype=str, keep_default_na=False)
    quality = pd.read_csv(quality_path, sep=";", dtype=str, keep_default_na=False)
    enriched = enrich_membership_from_quality(membership, quality)
    # Ensure schema columns exist without inventing PEA dates.
    defaults = {
        "membership_start": "", "membership_end": "", "pea_eligibility_start": "",
        "pea_eligibility_end": "", "trading_existence_end": "", "eligibility_source": "UNKNOWN",
    }
    for col, default in defaults.items():
        if col not in enriched.columns:
            enriched[col] = default
    enriched["promotion_eligible"] = False
    enriched_path = base / "ETF_PIT_MEMBERSHIP_V2.csv"
    enriched.to_csv(enriched_path, sep=";", index=False, encoding="utf-8-sig")
    report = validate_membership_table(enriched)
    report.update({
        "status": "PIT_PARTIAL_RECONSTRUCTION",
        "observed_history_can_prove_existence_not_pea_eligibility": True,
        "current_universe_survivorship_bias_unresolved": not report["promotion_ready"],
        "promotion_eligible": False,
        "block_reason": None if report["promotion_ready"] else "HISTORICAL_MEMBERSHIP_AND_PEA_ELIGIBILITY_NOT_FULLY_DOCUMENTED",
        "output": str(enriched_path.relative_to(root)),
    })
    report_path = base / "ETF_PIT_COVERAGE_REPORT.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build strict ETF PIT membership evidence layer")
    parser.parse_args(list(argv) if argv is not None else None)
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
