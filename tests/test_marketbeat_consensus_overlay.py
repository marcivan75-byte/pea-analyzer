import pandas as pd

from v182.decision.marketbeat_overlay import (
    _confirmation_state,
    _final_gate,
    _weighted_signal,
)
from v182.sources.marketbeat_parse import MarketBeatParseClient, parse_forecast


def _forecast_payload():
    def row(kind, current, month, quarter, year):
        return {
            "Type": kind,
            "Current Forecast8/7/25 to 8/7/26": current,
            "1 Month Ago7/8/25 to 7/8/26": month,
            "3 Months Ago5/9/25 to 5/9/26": quarter,
            "1 Year Ago8/7/24 to 8/7/25": year,
        }

    return {
        "status": "success",
        "data": {
            "consensus_rating_breakdown": [
                row("Strong Buy", "2Strong Buy rating(s)", "2Strong Buy rating(s)", "2Strong Buy rating(s)", "3Strong Buy rating(s)"),
                row("Buy", "3Buy rating(s)", "3Buy rating(s)", "3Buy rating(s)", "3Buy rating(s)"),
                row("Hold", "9Hold rating(s)", "10Hold rating(s)", "9Hold rating(s)", "2Hold rating(s)"),
                row("Sell", "1Sell rating(s)", "0Sell rating(s)", "1Sell rating(s)", "0Sell rating(s)"),
                row("Consensus Price Target", "$49.50", "$51.00", "$51.00", "$62.00"),
            ],
            "consensus_comparison": [],
            "summary": {},
        },
    }


def test_live_validated_forecast_schema_normalizes_target_and_consensus_changes():
    fields = parse_forecast(_forecast_payload())
    assert fields["mb_target_price"] == 49.5
    assert fields["mb_target_currency"] == "USD"
    assert fields["mb_target_1m_ago"] == 51.0
    assert round(fields["mb_target_change_1m_abs"], 2) == -1.50
    assert round(fields["mb_target_change_1m_pct"], 2) == -2.94
    assert round(fields["mb_target_change_12m_pct"], 2) == -20.16
    assert fields["mb_n_analysts"] == 15
    assert fields["mb_consensus_rating"] == "HOLD"
    assert fields["mb_consensus_delta_1m"] < 0


def test_safe_issuer_mapping_requires_local_ticker_evidence_before_adr_proxy():
    client = MarketBeatParseClient("dummy", min_interval_seconds=0)
    client.search_stocks = lambda query: [
        {"ticker": "SNY", "exchange": "NASDAQ", "name": "Sanofi"},
        {"ticker": "SAN", "exchange": "EPA", "name": "Sanofi"},
        {"ticker": "SNYNF", "exchange": "OTCMKTS", "name": "Sanofi"},
    ]
    mapping = client.resolve_issuer_listing("Sanofi", "SAN.PA")
    assert mapping is not None
    assert mapping["marketbeat_ticker"] == "SNY"
    assert mapping["marketbeat_exchange"] == "NASDAQ"
    assert mapping["local_marketbeat_ticker"] == "SAN"
    assert mapping["local_marketbeat_exchange"] == "EPA"
    assert mapping["match_type"] == "US_ANALYST_PROXY"


def test_adr_proxy_is_rejected_without_local_ticker_evidence():
    client = MarketBeatParseClient("dummy", min_interval_seconds=0)
    client.search_stocks = lambda query: [
        {"ticker": "SNY", "exchange": "NASDAQ", "name": "Sanofi"},
    ]
    assert client.resolve_issuer_listing("Sanofi", "SAN.PA") is None


def test_marketbeat_is_only_30_percent_when_local_revision_exists():
    assert _weighted_signal(10.0, -10.0) == 4.0
    assert _weighted_signal(None, -3.0) == -3.0
    assert _weighted_signal(4.0, None) == 4.0


def test_material_cross_source_opposition_is_flagged_as_divergence():
    assert _confirmation_state(5.0, -3.0) == "DIVERGENCE"
    assert _confirmation_state(-4.0, -6.0) == "CONFIRM_NEGATIVE"


def test_s1_cut_has_risk_precedence_even_if_marketbeat_is_positive():
    row = pd.Series({
        "target_change_run_pct": -6.0,
        "target_change_1m_pct": 3.0,
        "mb_target_change_1m_pct": 5.0,
        "target_upside_pct": 10.0,
        "revision_breadth_30d": 100.0,
    })
    cfg = {"thresholds": {
        "target_revision_strong_positive_pct": 5.0,
        "target_revision_positive_pct": 2.0,
        "target_revision_negative_pct": -2.0,
        "target_revision_strong_negative_pct": -5.0,
        "mandatory_review_target_cut_pct": -10.0,
    }}
    signal, gate, review, worst = _final_gate(row, cfg, 85.0, 6.0, 10.0)
    assert signal == "STRONG_NEGATIVE"
    assert gate == "PENALIZE_STRONG"
    assert review is False
    assert worst == -6.0


def test_severe_marketbeat_cut_can_force_review_without_replacing_local_price():
    row = pd.Series({
        "target_change_run_pct": 1.0,
        "target_change_1m_pct": 2.0,
        "mb_target_change_1m_pct": -11.0,
        "target_upside_pct": 25.0,
    })
    cfg = {"thresholds": {
        "target_revision_strong_positive_pct": 5.0,
        "target_revision_positive_pct": 2.0,
        "target_revision_negative_pct": -2.0,
        "target_revision_strong_negative_pct": -5.0,
        "mandatory_review_target_cut_pct": -10.0,
    }}
    signal, gate, review, worst = _final_gate(row, cfg, 70.0, -1.0, 0.0)
    assert gate == "BLOCK_NEW_BUY_REVIEW"
    assert review is True
    assert worst == -11.0
