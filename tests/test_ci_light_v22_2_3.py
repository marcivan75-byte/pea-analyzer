from __future__ import annotations

import pandas as pd

from v182.reporting import ci_light_v22_2_3 as light


def _row(**overrides):
    row = {
        "isin": "FR0000000001",
        "name": "Test",
        "asset_class": "ACTION",
        "horizon": "CT",
        "boursorama_consensus": "BUY",
        "boursorama_n_analysts": 11,
        "boursorama_target_upside_pct": 20.1,
        "tradingview_daily_signal": "BUY",
        "tradingview_weekly_signal": "STRONG_BUY",
        "tradingview_monthly_signal": "BUY",
        # Deliberately below full-CI thresholds: these fields must not gate LIGHT.
        "score": 1.0,
        "CI_CONFIDENCE_SCORE_V22_2_1": 1.0,
    }
    row.update(overrides)
    return pd.Series(row)


def test_light_accepts_exact_strict_source_contract():
    accepted, reasons, details = light._evaluate(_row())
    assert accepted is True
    assert reasons == []
    assert details["recommendation"] == "RENFORCER"
    assert details["analyst_count"] == 11
    assert details["upside"] == 20.1


def test_light_requires_strictly_more_than_ten_analysts():
    accepted, reasons, _ = light._evaluate(_row(boursorama_n_analysts=10))
    assert accepted is False
    assert "BOURSORAMA_ANALYST_COUNT_NOT_GT_10" in reasons


def test_light_requires_strictly_more_than_twenty_percent_upside():
    accepted, reasons, _ = light._evaluate(_row(boursorama_target_upside_pct=20.0))
    assert accepted is False
    assert "BOURSORAMA_TARGET_UPSIDE_NOT_GT_20" in reasons


def test_light_requires_all_three_tradingview_timeframes_positive():
    accepted, reasons, _ = light._evaluate(_row(tradingview_monthly_signal="NEUTRAL"))
    assert accepted is False
    assert "TRADINGVIEW_MONTHLY_NOT_BUY_OR_STRONG_BUY" in reasons


def test_light_missing_one_tradingview_timeframe_fails_closed():
    accepted, reasons, _ = light._evaluate(_row(tradingview_daily_signal=pd.NA))
    assert accepted is False
    assert "TRADINGVIEW_DAILY_SIGNAL_MISSING" in reasons


def test_light_etf_does_not_substitute_morningstar_for_boursorama_consensus():
    accepted, reasons, _ = light._evaluate(
        _row(asset_class="ETF", boursorama_consensus=pd.NA, morningstar_rating=5)
    )
    assert accepted is False
    assert "BOURSORAMA_NOT_ACHETER_OR_RENFORCER" in reasons


def test_light_etf_must_meet_same_analyst_and_upside_rules():
    accepted, reasons, _ = light._evaluate(
        _row(asset_class="ETF", boursorama_consensus="STRONG_BUY", boursorama_n_analysts=9)
    )
    assert accepted is False
    assert "BOURSORAMA_ANALYST_COUNT_NOT_GT_10" in reasons


def test_light_strong_buy_maps_to_acheter_and_buy_to_renforcer():
    assert light._boursorama_recommendation(_row(boursorama_consensus="STRONG_BUY"))[0] == "ACHETER"
    assert light._boursorama_recommendation(_row(boursorama_consensus="BUY"))[0] == "RENFORCER"


def test_light_rejects_hold_accumuler_and_other_non_requested_labels():
    for value in ("HOLD", "ACCUMULER", "ACHAT", "SELL"):
        accepted, reasons, _ = light._evaluate(_row(boursorama_consensus=value))
        assert accepted is False
        assert "BOURSORAMA_NOT_ACHETER_OR_RENFORCER" in reasons


def test_light_does_not_use_full_ci_score_or_confidence_as_admission_gate():
    accepted, reasons, _ = light._evaluate(_row(score=0.0, CI_CONFIDENCE_SCORE_V22_2_1=0.0))
    assert accepted is True
    assert reasons == []


def test_light_export_contains_three_signals_and_both_urls():
    frame = pd.DataFrame([
        {
            **_row().to_dict(),
            "CI_LIGHT_BOURSORAMA_RECOMMENDATION": "RENFORCER",
            "CI_LIGHT_BOURSORAMA_ANALYSTS": 11,
            "CI_LIGHT_BOURSORAMA_UPSIDE_PCT": 21,
            "CI_LIGHT_TRADINGVIEW_DAILY": "BUY",
            "CI_LIGHT_TRADINGVIEW_WEEKLY": "BUY",
            "CI_LIGHT_TRADINGVIEW_MONTHLY": "STRONG_BUY",
            "CI_LIGHT_BOURSORAMA_URL": "https://www.boursorama.com/cours/consensus/TEST/",
            "CI_LIGHT_TRADINGVIEW_URL": "https://www.tradingview.com/symbols/EURONEXT-TEST/technicals/",
        }
    ])
    columns = light._export_columns(frame)
    for required in (
        "CI_LIGHT_TRADINGVIEW_DAILY",
        "CI_LIGHT_TRADINGVIEW_WEEKLY",
        "CI_LIGHT_TRADINGVIEW_MONTHLY",
        "CI_LIGHT_BOURSORAMA_URL",
        "CI_LIGHT_TRADINGVIEW_URL",
    ):
        assert required in columns
