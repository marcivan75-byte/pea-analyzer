import pandas as pd

from v182.backtest.etf_pit_membership_v2 import enrich_membership_from_quality, row_is_pit_eligible, validate_membership_table


def test_observed_price_history_proves_existence_only():
    membership = pd.DataFrame([{ "isin":"FR0001", "membership_start":"", "membership_end":"", "pea_eligibility_start":"", "pea_eligibility_end":"", "membership_source":"CURRENT_MASTER_ONLY", "pit_status":"UNKNOWN_RESEARCH_ONLY", "promotion_eligible":False }])
    quality = pd.DataFrame([{ "isin":"FR0001", "first_date":"2014-01-02", "last_date":"2026-08-31", "rows":"3200", "mt_close_only_ready":"True" }])
    out = enrich_membership_from_quality(membership, quality)
    assert out.loc[0, "trading_existence_start"] == "2014-01-02"
    assert out.loc[0, "pea_eligibility_start"] == ""
    assert bool(out.loc[0, "promotion_eligible"]) is False


def test_unknown_dates_never_default_to_eligible():
    row = pd.Series({"membership_start":"", "membership_end":"", "pea_eligibility_start":"", "pea_eligibility_end":"", "trading_existence_start":"2014-01-02", "trading_existence_end":""})
    assert row_is_pit_eligible(row, "2020-01-01") is False


def test_strict_interval_gate():
    row = pd.Series({"membership_start":"2015-01-01", "membership_end":"2022-12-31", "pea_eligibility_start":"2016-01-01", "pea_eligibility_end":"2022-06-30", "trading_existence_start":"2014-01-02", "trading_existence_end":"2023-01-31"})
    assert row_is_pit_eligible(row, "2015-06-01") is False
    assert row_is_pit_eligible(row, "2016-06-01") is True
    assert row_is_pit_eligible(row, "2022-07-01") is False


def test_promotion_ready_requires_all_evidence():
    incomplete = pd.DataFrame([{
        "isin":"FR0001", "membership_start":"2015-01-01", "membership_end":"", "pea_eligibility_start":"",
        "pea_eligibility_end":"", "trading_existence_start":"2014-01-02", "trading_existence_end":"",
        "membership_source":"ARCHIVE", "eligibility_source":"", "pit_status":"PARTIAL", "promotion_eligible":False,
    }])
    report = validate_membership_table(incomplete)
    assert report["promotion_ready"] is False
    complete = incomplete.copy()
    complete.loc[0, "pea_eligibility_start"] = "2015-01-01"
    complete.loc[0, "eligibility_source"] = "BROKER_ARCHIVE"
    report2 = validate_membership_table(complete)
    assert report2["promotion_ready"] is True
