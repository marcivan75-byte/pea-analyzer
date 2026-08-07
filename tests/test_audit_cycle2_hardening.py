import json

import pandas as pd

from v182.decision.marketbeat_overlay import _select_marketbeat_rows
from v182.io.frames import apply_observations


def _obs(isin, field, value, source, evidence, as_of, status="AUTO_MATCH"):
    return {
        "universe": "ACTION",
        "isin": isin,
        "field": field,
        "value": value,
        "source": source,
        "collected_at": f"{as_of}T12:00:00+00:00",
        "as_of": as_of,
        "evidence_level": evidence,
        "validation_status": status,
    }


def test_batch_macro_fields_compare_against_original_freshness():
    frame = pd.DataFrame([{
        "isin": "FR1",
        "name": "Issuer",
        "macro_vix": "16.0",
        "macro_curve_10y2y": "0.20",
        "macro_as_of": "2026-08-01",
    }])
    observations = [
        _obs("FR1", "macro_vix", 15.15, "FRED", "A", "2026-08-06"),
        _obs("FR1", "macro_curve_10y2y", 0.44, "FRED", "A", "2026-08-06"),
    ]
    result, quarantine = apply_observations(frame, observations)
    assert quarantine == []
    assert result.loc[0, "macro_vix"] == "15.15"
    assert result.loc[0, "macro_curve_10y2y"] == "0.44"
    assert result.loc[0, "macro_as_of"] == "2026-08-06"


def test_batch_technical_fields_do_not_inherit_freshness_from_prior_field():
    frame = pd.DataFrame([{
        "isin": "FR1",
        "name": "Issuer",
        "mm50": "100",
        "mm200": "90",
        "ta_source": "INTERNAL_FROM_OHLCV_YFINANCE",
        "ta_as_of": "2026-08-01T00:00:00+00:00",
    }])
    observations = [
        _obs("FR1", "mm50", 101, "INTERNAL_FROM_OHLCV_YFINANCE", "C", "2026-08-06"),
        _obs("FR1", "mm200", 91, "INTERNAL_FROM_OHLCV_YFINANCE", "C", "2026-08-06"),
    ]
    result, quarantine = apply_observations(frame, observations)
    assert quarantine == []
    assert result.loc[0, "mm50"] == "101"
    assert result.loc[0, "mm200"] == "91"


def test_marketbeat_safe_proxy_status_is_mergeable_and_provenanced():
    frame = pd.DataFrame([{"isin": "FR1", "name": "Sanofi", "mb_target_price": pd.NA}])
    observation = _obs(
        "FR1", "mb_target_price", 49.5, "MarketBeat via Parse", "C", "2026-08-07",
        status="AUTO_MATCH_ISSUER_PROXY",
    )
    result, quarantine = apply_observations(frame, [observation])
    assert quarantine == []
    assert result.loc[0, "mb_target_price"] == "49.5"
    provenance = json.loads(result.loc[0, "_field_provenance_json"])
    assert provenance["mb_target_price"]["source"] == "MarketBeat via Parse"


def test_marketbeat_selection_prefers_analyst_depth_before_raw_score():
    frame = pd.DataFrame([
        {"isin": "A", "name": "High Score Thin", "yahoo_ticker": "A.PA", "score_brut": "99", "n_analysts": "2", "comite_status": "WATCH"},
        {"isin": "B", "name": "Deep Coverage", "yahoo_ticker": "B.PA", "score_brut": "70", "n_analysts": "40", "comite_status": "WATCH"},
        {"isin": "C", "name": "Medium Coverage", "yahoo_ticker": "C.PA", "score_brut": "80", "n_analysts": "20", "comite_status": "WATCH"},
    ])
    selected = _select_marketbeat_rows(frame, 2)
    assert [row["isin"] for row in selected] == ["B", "C"]
