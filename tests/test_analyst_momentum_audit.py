import json

import pandas as pd

from v182.decision.analyst_momentum import (
    SNAPSHOT_COLUMNS,
    _signal_and_gate,
    enrich_analyst_momentum,
)


def _cfg():
    return {
        "committee": {
            "analyst_momentum": {
                "overall_weight": 0.15,
                "thresholds": {
                    "target_revision_strong_positive_pct": 5.0,
                    "target_revision_positive_pct": 2.0,
                    "target_revision_negative_pct": -2.0,
                    "target_revision_strong_negative_pct": -5.0,
                    "mandatory_review_target_cut_pct": -10.0,
                },
            }
        }
    }


def test_no_analyst_data_preserves_base_committee_score():
    actions = pd.DataFrame([{
        "isin": "FR0000000100",
        "name": "NO ANALYST DATA",
        "score_brut": "80",
        "comite_status": "COMMITTEE",
    }])

    out, _, metrics = enrich_analyst_momentum(
        actions,
        cfg=_cfg(),
        run_date="2026-08-07",
    )

    row = out.iloc[0]
    assert pd.isna(row["analyst_momentum_score"])
    assert float(row["committee_score_with_analyst_momentum"]) == 80.0
    assert row["committee_analyst_gate"] == "NEUTRAL"
    assert bool(row["committee_review_required"]) is False
    assert metrics["no_analyst_data"] == 1


def test_strong_target_cut_cannot_be_overridden_by_high_composite_score():
    signal, gate, review = _signal_and_gate(
        {
            "target_change_1m_pct": -6.0,
            "analyst_momentum_score": 82.0,
            "consensus_delta_1m": 20.0,
            "revision_breadth_30d": 100.0,
            "target_upside_pct": 10.0,
        },
        _cfg()["committee"]["analyst_momentum"],
    )

    assert signal == "STRONG_NEGATIVE"
    assert gate == "PENALIZE_STRONG"
    assert review is False


def test_history_does_not_compare_targets_across_canonical_sources():
    actions = pd.DataFrame([{
        "isin": "FR0000000101",
        "target_price": "100",
        "last_close": "90",
        "consensus_rating": "BUY",
        "consensus_score": "4",
        "consensus_source": "Finnhub",
        "score_brut": "70",
    }])
    history = pd.DataFrame([{
        "date": "2026-07-01",
        "isin": "FR0000000101",
        "source": "yfinance",
        "consensus_rating": "HOLD",
        "consensus_score_100": "50",
        "n_analysts": "10",
        "strong_buy": None,
        "buy": None,
        "hold": None,
        "sell": None,
        "strong_sell": None,
        "target_low": "70",
        "target_mean": "80",
        "target_high": "90",
        "last_close": "75",
    }], columns=SNAPSHOT_COLUMNS)

    out, _, metrics = enrich_analyst_momentum(
        actions,
        history=history,
        cfg=_cfg(),
        run_date="2026-08-07",
    )

    row = out.iloc[0]
    assert pd.isna(row["target_change_1m_pct"])
    assert pd.isna(row["consensus_delta_1m"])
    assert metrics["history_comparison_policy"] == "SAME_CANONICAL_SOURCE_ONLY"


def test_consensus_freshness_uses_field_provenance_not_fundamental_timestamp():
    provenance = json.dumps({
        "target_price": {
            "source": "Finnhub",
            "evidence_level": "B",
            "as_of": "2026-08-05",
        }
    })
    actions = pd.DataFrame([{
        "isin": "FR0000000102",
        "target_price": "100",
        "last_close": "90",
        "consensus_rating": "BUY",
        "consensus_score": "4",
        "consensus_source": "Finnhub",
        "fundamentals_as_of": "2026-08-07",
        "_field_provenance_json": provenance,
    }])

    out, _, _ = enrich_analyst_momentum(
        actions,
        cfg=_cfg(),
        run_date="2026-08-07",
    )

    row = out.iloc[0]
    assert row["consensus_as_of"] == "2026-08-05"
    assert int(row["consensus_source_count"]) == 1
