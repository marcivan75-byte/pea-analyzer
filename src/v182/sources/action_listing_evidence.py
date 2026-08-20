from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import re
from typing import Any

import pandas as pd

from v182.sources.euronext_ipo_v1_3 import EURONEXT_IPO_ALL, collect_euronext_v1_3

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKLIST = ROOT / "config" / "V21_17_ACTION_SHORT_HISTORY_WORKLIST.csv"
LISTING_CONFLICT_TOLERANCE_DAYS = 7


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "nan", "None", "<NA>", "N/A", "NA", "NULL"}:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    # Euronext detail pages use European day-first representations such as
    # "Thu 09/07/2026". Treat slash/dot dates as day-first deterministically.
    dayfirst = bool(re.search(r"\d{1,2}[/.]\d{1,2}[/.]\d{4}", text))
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst)
    if pd.isna(parsed):
        return None
    return parsed.date()


def load_worklist(path: str | Path = DEFAULT_WORKLIST) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    required = {"isin", "ticker", "first_observed_date", "source_run", "initial_status"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"ACTION_LISTING_WORKLIST_MISSING_COLUMNS:{','.join(sorted(missing))}")
    if frame["isin"].isna().any() or frame["isin"].duplicated().any():
        raise ValueError("ACTION_LISTING_WORKLIST_ISIN_NOT_UNIQUE")
    return frame.copy()


def _candidate_listing_date(candidate: dict[str, Any]) -> date | None:
    table_date = _parse_date(candidate.get("expected_date"))
    detail_date = _parse_date(candidate.get("euronext_ipo_date_text"))
    if table_date and detail_date and table_date != detail_date:
        return None
    return detail_date or table_date


def qualify_euronext_candidates(
    worklist: pd.DataFrame,
    candidates: list[dict[str, Any]],
    *,
    as_of: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    targets = {
        str(row["isin"]).strip().upper(): {
            "ticker": str(row.get("ticker") or "").strip(),
            "first_observed_date": _parse_date(row.get("first_observed_date")),
        }
        for _, row in worklist.iterrows()
        if str(row.get("isin") or "").strip()
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ignored_non_target = 0
    rejected_missing_isin = 0

    for candidate in candidates:
        isin = str(candidate.get("isin") or "").strip().upper()
        if not isin:
            rejected_missing_isin += 1
            continue
        if isin not in targets:
            ignored_non_target += 1
            continue
        grouped[isin].append(candidate)

    accepted: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []

    for isin, target in targets.items():
        rows = grouped.get(isin, [])
        if not rows:
            continue

        parsed_rows: list[tuple[date, dict[str, Any]]] = []
        malformed_rows: list[dict[str, Any]] = []
        for candidate in rows:
            listing_date = _candidate_listing_date(candidate)
            if listing_date is None:
                malformed_rows.append(candidate)
            else:
                parsed_rows.append((listing_date, candidate))

        if malformed_rows:
            quarantine.append({
                "isin": isin,
                "ticker": target["ticker"],
                "status": "QUARANTINE",
                "reason": "OFFICIAL_LISTING_DATE_MISSING_OR_CONFLICTING",
                "source_name": "EURONEXT_OFFICIAL_IPO_SHOWCASE",
                "source_url": str(malformed_rows[0].get("euronext_showcase_url") or EURONEXT_IPO_ALL),
            })
            continue

        unique_dates = sorted({value for value, _ in parsed_rows})
        if len(unique_dates) != 1:
            quarantine.append({
                "isin": isin,
                "ticker": target["ticker"],
                "status": "QUARANTINE",
                "reason": "MULTIPLE_OFFICIAL_LISTING_DATES",
                "listing_dates": ",".join(value.isoformat() for value in unique_dates),
                "source_name": "EURONEXT_OFFICIAL_IPO_SHOWCASE",
                "source_url": EURONEXT_IPO_ALL,
            })
            continue

        listing_date = unique_dates[0]
        if listing_date > as_of:
            quarantine.append({
                "isin": isin,
                "ticker": target["ticker"],
                "status": "QUARANTINE",
                "reason": "OFFICIAL_LISTING_DATE_IN_FUTURE",
                "official_listing_date": listing_date.isoformat(),
                "source_name": "EURONEXT_OFFICIAL_IPO_SHOWCASE",
                "source_url": EURONEXT_IPO_ALL,
            })
            continue

        first_observed = target["first_observed_date"]
        if first_observed is None:
            quarantine.append({
                "isin": isin,
                "ticker": target["ticker"],
                "status": "QUARANTINE",
                "reason": "TARGET_FIRST_OBSERVED_DATE_MISSING",
                "official_listing_date": listing_date.isoformat(),
                "source_name": "EURONEXT_OFFICIAL_IPO_SHOWCASE",
                "source_url": EURONEXT_IPO_ALL,
            })
            continue
        if listing_date > first_observed + timedelta(days=LISTING_CONFLICT_TOLERANCE_DAYS):
            quarantine.append({
                "isin": isin,
                "ticker": target["ticker"],
                "status": "QUARANTINE",
                "reason": "OFFICIAL_LISTING_DATE_AFTER_FIRST_OBSERVATION",
                "official_listing_date": listing_date.isoformat(),
                "first_observed_date": first_observed.isoformat(),
                "source_name": "EURONEXT_OFFICIAL_IPO_SHOWCASE",
                "source_url": EURONEXT_IPO_ALL,
            })
            continue

        candidate = parsed_rows[0][1]
        accepted.append({
            "isin": isin,
            "ticker": target["ticker"],
            "official_listing_date": listing_date.isoformat(),
            "first_observed_date": first_observed.isoformat(),
            "exchange": str(candidate.get("exchange") or "").strip(),
            "euronext_location": str(candidate.get("euronext_location") or "").strip(),
            "official_symbol": str(candidate.get("symbol") or "").strip(),
            "official_name": str(candidate.get("name") or "").strip(),
            "source_name": "EURONEXT_OFFICIAL_IPO_SHOWCASE",
            "source_url": str(candidate.get("euronext_showcase_url") or EURONEXT_IPO_ALL),
            "evidence_level": "A",
            "validation_status": "EXACT_ISIN_OFFICIAL_LISTING_DATE",
        })

    accepted_isins = {row["isin"] for row in accepted}
    quarantined_isins = {row["isin"] for row in quarantine}
    unresolved = sorted(set(targets).difference(accepted_isins).difference(quarantined_isins))
    metrics = {
        "status": "SUCCESS",
        "source": "EURONEXT_OFFICIAL_IPO_SHOWCASE",
        "requested_targets": len(targets),
        "official_candidates_received": len(candidates),
        "accepted_exact_isin": len(accepted),
        "quarantine_rows": len(quarantine),
        "unresolved_targets": len(unresolved),
        "unresolved_isins": unresolved,
        "ignored_non_target_candidates": ignored_non_target,
        "rejected_missing_isin_candidates": rejected_missing_isin,
        "synthetic_history_created": False,
        "calibration_eligibility_changed": False,
    }
    return accepted, quarantine, metrics


def collect_action_listing_evidence(
    *,
    worklist_path: str | Path = DEFAULT_WORKLIST,
    start: date = date(2023, 1, 1),
    end: date,
    timeout: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    worklist = load_worklist(worklist_path)
    candidates, source_metrics = collect_euronext_v1_3(start, end, timeout=timeout)
    accepted, quarantine, metrics = qualify_euronext_candidates(worklist, candidates, as_of=end)
    metrics["source_metrics"] = source_metrics
    return accepted, quarantine, metrics
