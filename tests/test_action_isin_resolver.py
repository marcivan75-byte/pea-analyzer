from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.audit.canonical_universe import IDENTITY_ONLY_STATUS
from v182.mapping.action_isin_resolver import (
    HYDRATED_STATUS,
    YahooIdentity,
    apply_identity_overlay,
    candidate_yahoo_ticker,
    name_similarity,
    resolve_identity_rows,
)


def _validator(ticker: str, expected_name: str):
    if ticker == "TEST.PA":
        return YahooIdentity(ticker=ticker, quote_type="EQUITY", name="Test Société", currency="EUR", exchange="PAR", similarity=0.98)
    return None


def _searcher(_isin: str):
    return {
        "symbol": "TEST.PA",
        "quoteType": "EQUITY",
        "longname": "Test Société",
        "exchange": "PAR",
    }


def test_candidate_requires_supported_equity_exchange() -> None:
    assert candidate_yahoo_ticker({"ticker":"TEST","exchCode":"FP","marketSector":"Equity","securityType":"Common Stock"}) == "TEST.PA"
    assert candidate_yahoo_ticker({"ticker":"TEST","exchCode":"XX","marketSector":"Equity","securityType":"Common Stock"}) == ""
    assert candidate_yahoo_ticker({"ticker":"TEST","exchCode":"FP","marketSector":"Equity","securityType":"Warrant"}) == ""


def test_name_similarity_handles_legal_suffixes() -> None:
    assert name_similarity("Test Société SA", "TEST SOCIETE") > 0.95


def test_resolver_promotes_only_cross_validated_exact_isin_identity() -> None:
    frame = pd.DataFrame([{"isin":"FR0013412038","name":pd.NA,"yahoo_ticker":pd.NA,"canonical_seed_status":IDENTITY_ONLY_STATUS}])
    matches = {
        "FR0013412038":[{
            "figi":"BBG000TEST01","shareClassFIGI":"BBG001TEST01","name":"Test Société SA","ticker":"TEST",
            "exchCode":"FP","marketSector":"Equity","securityType":"Common Stock",
        }]
    }
    overlay, gaps = resolve_identity_rows(
        frame,
        openfigi_matches=matches,
        yahoo_isin_searcher=_searcher,
        yahoo_validator=_validator,
    )
    assert gaps.empty
    assert len(overlay) == 1
    row = overlay.iloc[0]
    assert row["yahoo_ticker"] == "TEST.PA"
    assert row["canonical_seed_status"] == HYDRATED_STATUS
    assert row["identity_resolution_status"] == "VALIDATED"
    assert "Yahoo_Search_ISIN_rank1" in row["identity_source"]


def test_openfigi_alternate_venues_do_not_create_ambiguity_when_yahoo_isin_search_is_exact() -> None:
    frame = pd.DataFrame([{"isin":"FR0013412038","name":pd.NA,"yahoo_ticker":pd.NA,"canonical_seed_status":IDENTITY_ONLY_STATUS}])
    matches = {
        "FR0013412038":[
            {"figi":"A","name":"Test Société","ticker":"TEST","exchCode":"FP","marketSector":"Equity","securityType":"Common Stock"},
            {"figi":"B","name":"Test Société","ticker":"TEST2","exchCode":"GR","marketSector":"Equity","securityType":"Common Stock"},
        ]
    }
    overlay, gaps = resolve_identity_rows(
        frame,
        openfigi_matches=matches,
        yahoo_isin_searcher=_searcher,
        yahoo_validator=_validator,
    )
    assert gaps.empty
    assert overlay.iloc[0]["yahoo_ticker"] == "TEST.PA"


def test_yahoo_isin_name_mismatch_remains_unresolved() -> None:
    frame = pd.DataFrame([{"isin":"FR0013412038","name":pd.NA,"yahoo_ticker":pd.NA,"canonical_seed_status":IDENTITY_ONLY_STATUS}])
    matches = {
        "FR0013412038":[{"name":"Test Société","ticker":"TEST","exchCode":"FP","marketSector":"Equity","securityType":"Common Stock"}]
    }
    bad_search=lambda _isin: {"symbol":"OTHER.PA","quoteType":"EQUITY","longname":"Completely Different Issuer","exchange":"PAR"}
    overlay, gaps = resolve_identity_rows(
        frame,
        openfigi_matches=matches,
        yahoo_isin_searcher=bad_search,
        yahoo_validator=lambda *_: None,
    )
    assert overlay.empty
    assert len(gaps) == 1
    assert gaps.iloc[0]["reason"] == "YAHOO_ISIN_SEARCH_NAME_MISMATCH"


def test_yahoo_no_rank1_adds_name_only_but_keeps_blocked() -> None:
    frame = pd.DataFrame([{"isin":"FR0013412038","name":pd.NA,"yahoo_ticker":pd.NA,"canonical_seed_status":IDENTITY_ONLY_STATUS}])
    matches = {
        "FR0013412038":[{"name":"Test Société","ticker":"TEST","exchCode":"FP","marketSector":"Equity","securityType":"Common Stock"}]
    }
    overlay, gaps = resolve_identity_rows(
        frame,
        openfigi_matches=matches,
        yahoo_isin_searcher=lambda _isin: None,
        yahoo_validator=_validator,
    )
    assert len(overlay) == 1
    assert overlay.iloc[0]["identity_resolution_status"] == "OPENFIGI_NAME_ONLY_TICKER_UNRESOLVED"
    assert len(gaps) == 1
    assert gaps.iloc[0]["reason"] == "YAHOO_ISIN_SEARCH_NO_EQUITY_RANK1"


def test_name_only_openfigi_does_not_make_row_scorable(tmp_path: Path) -> None:
    frame = pd.DataFrame([{"isin":"FR0013412038","name":pd.NA,"yahoo_ticker":pd.NA,"canonical_seed_status":IDENTITY_ONLY_STATUS}])
    overlay = pd.DataFrame([{
        "isin":"FR0013412038","name":"Test Société","yahoo_ticker":"",
        "canonical_seed_status":IDENTITY_ONLY_STATUS,
        "identity_resolution_status":"OPENFIGI_NAME_ONLY_TICKER_UNRESOLVED",
        "identity_source":"OpenFIGI_ID_ISIN","identity_validation_as_of":"2026-08-19T20:00:00+00:00",
    }])
    path = tmp_path / "overlay.csv"
    overlay.to_csv(path, sep=";", encoding="utf-8-sig", index=False)
    enriched, audit = apply_identity_overlay(frame, path)
    row = enriched.iloc[0]
    assert row["name"] == "Test Société"
    assert pd.isna(row["yahoo_ticker"])
    assert row["canonical_seed_status"] == IDENTITY_ONLY_STATUS
    assert audit["fully_hydrated"] == 0


def test_validated_overlay_transitions_row_to_market_data_eligible(tmp_path: Path) -> None:
    frame = pd.DataFrame([{"isin":"FR0013412038","name":pd.NA,"yahoo_ticker":pd.NA,"canonical_seed_status":IDENTITY_ONLY_STATUS}])
    overlay = pd.DataFrame([{
        "isin":"FR0013412038","name":"Test Société","yahoo_ticker":"TEST.PA",
        "canonical_seed_status":HYDRATED_STATUS,"identity_resolution_status":"VALIDATED",
        "identity_source":"OpenFIGI_ID_ISIN+Yahoo_Search_ISIN_rank1+Yahoo_Ticker_validation",
        "identity_validation_as_of":"2026-08-19T20:00:00+00:00",
    }])
    path = tmp_path / "overlay.csv"
    overlay.to_csv(path, sep=";", encoding="utf-8-sig", index=False)
    enriched, audit = apply_identity_overlay(frame, path)
    row = enriched.iloc[0]
    assert row["yahoo_ticker"] == "TEST.PA"
    assert row["canonical_seed_status"] == HYDRATED_STATUS
    assert audit["fully_hydrated"] == 1
