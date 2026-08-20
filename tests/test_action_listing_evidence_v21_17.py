from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path

import pandas as pd

from v182.sources.action_listing_evidence import (
    _candidate_listing_date,
    load_worklist,
    qualify_euronext_candidates,
)

WORKLIST = Path("config/V21_17_ACTION_SHORT_HISTORY_WORKLIST.csv")
EXPECTED_LOGICAL_SHA256 = "a2c446f7d82de44c6dd8218923000d93914b8b7fae03e99b4dc1f196cf4f2ba1"


def _logical_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _worklist(*rows: tuple[str, str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "isin": isin,
                "ticker": ticker,
                "first_observed_date": first,
                "source_run": "32379163874",
                "initial_status": "START_AFTER_ANCHOR_UNRESOLVED",
            }
            for isin, ticker, first in rows
        ]
    )


def _candidate(isin: str, listing_date: str, *, detail_date: str = "") -> dict:
    return {
        "isin": isin,
        "name": "Example SA",
        "symbol": "EXMPL",
        "exchange": "Euronext Growth",
        "expected_date": listing_date,
        "euronext_ipo_date_text": detail_date,
        "euronext_location": "Paris",
        "euronext_showcase_url": "https://live.euronext.com/en/ipo-showcase/example",
    }


def test_frozen_worklist_is_exact_real_v21_13_population() -> None:
    frame = load_worklist(WORKLIST)
    assert len(frame) == 157
    assert frame["isin"].nunique() == 157
    assert set(frame["source_run"]) == {"32379163874"}
    assert set(frame["initial_status"]) == {"START_AFTER_ANCHOR_UNRESOLVED"}
    assert _logical_sha256(WORKLIST) == EXPECTED_LOGICAL_SHA256


def test_euronext_european_detail_date_is_day_first() -> None:
    candidate = _candidate("NO0013756361", "2026-07-09", detail_date="Thu 09/07/2026")
    assert _candidate_listing_date(candidate) == date(2026, 7, 9)


def test_accepts_only_exact_target_isin_official_listing_date() -> None:
    worklist = _worklist(("NO0013756361", "POLAR.OL", "2026-07-09"))
    accepted, quarantine, metrics = qualify_euronext_candidates(
        worklist,
        [
            _candidate("NO0013756361", "2026-07-09", detail_date="Thu 09/07/2026"),
            _candidate("FR0000000000", "2026-07-09"),
            _candidate("", "2026-07-09"),
        ],
        as_of=date(2026, 8, 20),
    )
    assert len(accepted) == 1
    assert accepted[0]["isin"] == "NO0013756361"
    assert accepted[0]["official_listing_date"] == "2026-07-09"
    assert accepted[0]["validation_status"] == "EXACT_ISIN_OFFICIAL_LISTING_DATE"
    assert quarantine == []
    assert metrics["ignored_non_target_candidates"] == 1
    assert metrics["rejected_missing_isin_candidates"] == 1
    assert metrics["calibration_eligibility_changed"] is False
    assert metrics["synthetic_history_created"] is False


def test_future_official_listing_date_is_quarantined() -> None:
    worklist = _worklist(("FR0014018Y10", "ALLSF.PA", "2026-07-14"))
    accepted, quarantine, metrics = qualify_euronext_candidates(
        worklist,
        [_candidate("FR0014018Y10", "2026-09-01")],
        as_of=date(2026, 8, 20),
    )
    assert accepted == []
    assert quarantine[0]["reason"] == "OFFICIAL_LISTING_DATE_IN_FUTURE"
    assert metrics["unresolved_targets"] == 0


def test_listing_after_first_market_observation_is_quarantined() -> None:
    worklist = _worklist(("FR0014018Y10", "ALLSF.PA", "2026-07-14"))
    accepted, quarantine, _ = qualify_euronext_candidates(
        worklist,
        [_candidate("FR0014018Y10", "2026-07-30")],
        as_of=date(2026, 8, 20),
    )
    assert accepted == []
    assert quarantine[0]["reason"] == "OFFICIAL_LISTING_DATE_AFTER_FIRST_OBSERVATION"


def test_conflicting_official_dates_for_same_isin_are_quarantined() -> None:
    worklist = _worklist(("NO0013756361", "POLAR.OL", "2026-07-09"))
    accepted, quarantine, _ = qualify_euronext_candidates(
        worklist,
        [
            _candidate("NO0013756361", "2026-07-08"),
            _candidate("NO0013756361", "2026-07-09"),
        ],
        as_of=date(2026, 8, 20),
    )
    assert accepted == []
    assert quarantine[0]["reason"] == "MULTIPLE_OFFICIAL_LISTING_DATES"


def test_unmatched_target_remains_unresolved() -> None:
    worklist = _worklist(
        ("NO0013756361", "POLAR.OL", "2026-07-09"),
        ("CZ0009008942", "COLT.PR", "2026-04-07"),
    )
    accepted, quarantine, metrics = qualify_euronext_candidates(
        worklist,
        [_candidate("NO0013756361", "2026-07-09")],
        as_of=date(2026, 8, 20),
    )
    assert len(accepted) == 1
    assert quarantine == []
    assert metrics["unresolved_targets"] == 1
    assert metrics["unresolved_isins"] == ["CZ0009008942"]
