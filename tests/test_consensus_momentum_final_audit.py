from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from v182.decision.analyst_momentum import (
    REVISION_COLUMNS,
    _broker_weight_map,
    _committee_selection,
    _revision_metrics,
    consensus_score_100,
)
from v182.sources.marketbeat_parse import MarketBeatParseClient, MarketBeatParseError


def test_yahoo_continuous_recommendation_mean_precedes_discrete_consensus_score():
    row = pd.Series({
        "consensus_score": "4.0",
        "recommendation_mean_yf": "2.2",
        "recommendation_key_yf": "buy",
    })
    # Yahoo 2.2 on the 1=Strong Buy / 5=Strong Sell scale maps to 70/100.
    # Using the discrete BUY bucket instead would incorrectly flatten it to 75.
    assert consensus_score_100(row) == 70.0


def test_committee_selection_falls_back_to_top_300_when_statuses_are_empty():
    frame = pd.DataFrame([
        {"isin": f"FR{i:010d}", "score_brut": str(i), "comite_status": "NONE"}
        for i in range(400)
    ])
    selected, basis = _committee_selection(frame)
    assert len(selected) == 300
    assert basis == "TOP_300_SCORE_BRUT_FALLBACK"
    assert float(selected.iloc[0]["score_brut"]) == 399.0
    assert float(selected.iloc[-1]["score_brut"]) == 100.0


def test_committee_selection_prefers_explicit_committee_watch():
    frame = pd.DataFrame([
        {"isin": "A", "score_brut": "10", "comite_status": "NONE"},
        {"isin": "B", "score_brut": "5", "comite_status": "WATCH"},
        {"isin": "C", "score_brut": "1", "comite_status": "COMMITTEE"},
    ])
    selected, basis = _committee_selection(frame)
    assert set(selected["isin"]) == {"B", "C"}
    assert basis == "EXPLICIT_COMMITTEE_WATCH"


def test_broker_aliases_receive_reputation_weight(tmp_path):
    weights_path = tmp_path / "weights.csv"
    weights_path.write_text(
        "broker;weight;status;note\nJ.P. Morgan;1.30;CONFIRMED;Top tier\n",
        encoding="utf-8",
    )
    weights = _broker_weight_map(weights_path)
    revisions = pd.DataFrame([{
        "date": "2026-08-01",
        "isin": "FR1",
        "broker": "JPMorgan Chase & Co.",
        "analyst": "A",
        "old_rating": "HOLD",
        "new_rating": "BUY",
        "old_target": "100",
        "new_target": "110",
        "change_abs": "10",
        "change_pct": "10",
        "currency": "EUR",
        "source": "TEST",
    }], columns=REVISION_COLUMNS)
    metrics = _revision_metrics(revisions, "FR1", pd.Timestamp("2026-08-07", tz="UTC"), weights)
    assert metrics["weighted_target_revision_30d_pct"] == 10.0
    assert metrics["weighted_consensus_delta_30d"] == 25.0
    assert weights.get("jpmorgan") == 1.30


def test_marketbeat_parse_requires_key():
    with pytest.raises(ValueError, match="PARSE_API_KEY"):
        MarketBeatParseClient("")


def test_marketbeat_parse_429_is_sanitized_without_key_leak():
    response = Mock()
    response.status_code = 429
    session = Mock()
    session.get.return_value = response
    client = MarketBeatParseClient("secret-not-to-log", min_interval_seconds=0, session=session)
    with pytest.raises(MarketBeatParseError, match="PARSE_RATE_LIMIT") as exc:
        client.search_stocks("Sanofi")
    assert "secret-not-to-log" not in str(exc.value)
    _, kwargs = session.get.call_args
    assert kwargs["headers"]["X-API-Key"] == "secret-not-to-log"


def test_marketbeat_parse_resolve_issuer_uses_name_match():
    response = Mock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "status": "success",
        "data": {
            "items": [
                {"name": "Unrelated Corp", "ticker": "XXX"},
                {"name": "Sanofi SA", "ticker": "SNY", "exchange": "NASDAQ"},
            ]
        },
    }
    session = Mock()
    session.get.return_value = response
    client = MarketBeatParseClient("key", min_interval_seconds=0, session=session)
    resolved = client.resolve_issuer("Sanofi")
    assert resolved is not None
    assert resolved["ticker"] == "SNY"
    assert resolved["_match_score"] >= 0.72


def test_online_workflow_restores_persistent_consensus_history():
    text = Path(".github/workflows/V18.2_online.yml").read_text(encoding="utf-8")
    assert "actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830" in text
    assert "outputs/history" in text
    assert "v182-consensus-history-" in text
