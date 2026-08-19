from datetime import date
from pathlib import Path

import pandas as pd

from v182.audit.canonical_universe import filter_actions
from v182.audit.master_data import audit_frame, isin_checksum_valid, normalize_isin
from v182.io.frames import load_master

ROOT = Path(__file__).resolve().parents[1]


def test_isin_checksum_known_valid_and_invalid() -> None:
    assert normalize_isin(" fr0013412038 ") == "FR0013412038"
    assert isin_checksum_valid("FR0013412038")
    assert isin_checksum_valid("IE00BKX55S42")
    assert not isin_checksum_valid("IE00BKW55S42")
    assert not isin_checksum_valid("FR0013412039")
    assert not isin_checksum_valid("NOT_AN_ISIN")


def test_structural_errors_are_fail_closed() -> None:
    frame = pd.DataFrame(
        [
            {"isin": "FR0013412038", "name": "A", "yahoo_ticker": "A.PA", "asset_class": "ACTION"},
            {"isin": "FR0013412038", "name": "B", "yahoo_ticker": "B.PA", "asset_class": "ACTION"},
            {"isin": "FR0013412039", "name": "C", "yahoo_ticker": "C.PA", "asset_class": "ETF"},
        ]
    )
    result = audit_frame(frame, "ACTION", today=date(2026, 8, 19))
    fatal_codes = set(result.issues.loc[result.issues["severity"].eq("FATAL"), "code"])
    assert "DUPLICATE_ISIN" in fatal_codes
    assert "ISIN_CHECKSUM_INVALID" in fatal_codes
    assert "ASSET_CLASS_MISMATCH" in fatal_codes


def test_identity_only_rows_are_blocked_not_invented() -> None:
    frame = pd.DataFrame(
        [
            {
                "isin": "FR0013412038",
                "name": pd.NA,
                "yahoo_ticker": pd.NA,
                "asset_class": "ACTION",
                "canonical_seed_status": "WHITELIST_ONLY_MISSING_METADATA",
            }
        ]
    )
    result = audit_frame(frame, "ACTION", today=date(2026, 8, 19))
    assert result.summary["identity_only_rows"] == 1
    assert result.summary["issue_counts"]["FATAL"] == 0
    assert result.summary["issue_counts"]["BLOCK_DATA"] >= 2


def test_numeric_and_future_date_anomalies_are_detected() -> None:
    frame = pd.DataFrame(
        [
            {
                "isin": "FR0013412038",
                "name": "ETF",
                "yahoo_ticker": "PCEU.PA",
                "primary_mic": "XPAR",
                "trading_currency": "EUR",
                "asset_class": "ETF",
                "ter_pct": "-0.10",
                "risk_indicator": "8",
                "as_of_date": "2026-08-20",
            }
        ]
    )
    result = audit_frame(frame, "ETF", today=date(2026, 8, 19))
    by_code = set(result.issues["code"])
    assert "NUMERIC_BELOW_BOUND" in by_code
    assert "NUMERIC_ABOVE_BOUND" in by_code
    assert "DATE_IN_FUTURE" in by_code


def test_real_canonical_masters_have_exact_universe_and_valid_isin_checksums() -> None:
    legacy_actions = load_master(ROOT / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    canonical = filter_actions(legacy_actions, ROOT / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts")
    etf = load_master(ROOT / "inputs" / "V18.2_PEA_ETF_MASTER.csv")

    action_result = audit_frame(canonical.included, "ACTION", today=date(2026, 8, 19))
    etf_result = audit_frame(etf, "ETF", today=date(2026, 8, 19))

    assert len(canonical.included) == 1829
    assert canonical.included["isin"].nunique() == 1829
    assert len(etf) == 102
    assert etf["isin"].nunique() == 102
    assert action_result.summary["invalid_or_missing_isin"] == 0
    assert etf_result.summary["invalid_or_missing_isin"] == 0
    assert action_result.summary["issue_counts"]["FATAL"] == 0
    assert etf_result.summary["issue_counts"]["FATAL"] == 0
