import pandas as pd

from v182.decision.analyst_momentum import (
    REVISION_COLUMNS,
    SNAPSHOT_COLUMNS,
    consensus_score_100,
    enrich_analyst_momentum,
)


def _cfg():
    return {
        "committee": {
            "analyst_momentum": {
                "overall_weight": 0.15,
                "weights": {
                    "target_revision": 0.35,
                    "consensus_change": 0.20,
                    "target_upside": 0.15,
                    "revision_breadth": 0.15,
                    "broker_quality": 0.10,
                    "confidence": 0.05,
                },
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


def test_consensus_score_100_uses_rating_counts():
    row = pd.Series({
        "analyst_strong_buy": 2,
        "analyst_buy": 4,
        "analyst_hold": 2,
        "analyst_sell": 0,
        "analyst_strong_sell": 0,
    })
    assert consensus_score_100(row) == 75.0


def test_target_upside_and_revision_are_distinct():
    actions = pd.DataFrame([{
        "isin": "FR0000000001",
        "name": "TEST",
        "last_close": "90",
        "target_price": "105",
        "consensus_rating": "BUY",
        "consensus_score": "4",
        "n_analysts": "12",
        "score_brut": "70",
        "comite_status": "COMMITTEE",
    }])
    history = pd.DataFrame([{
        "date": "2026-07-01",
        "isin": "FR0000000001",
        "source": "yfinance",
        "consensus_rating": "HOLD",
        "consensus_score_100": "50",
        "n_analysts": "10",
        "strong_buy": None,
        "buy": None,
        "hold": None,
        "sell": None,
        "strong_sell": None,
        "target_low": "80",
        "target_mean": "100",
        "target_high": "110",
        "last_close": "92",
    }], columns=SNAPSHOT_COLUMNS)

    out, _, _ = enrich_analyst_momentum(
        actions,
        history=history,
        revisions=pd.DataFrame(columns=REVISION_COLUMNS),
        cfg=_cfg(),
        run_date="2026-08-07",
    )
    row = out.iloc[0]
    assert round(float(row["target_upside_abs"]), 2) == 15.0
    assert round(float(row["target_upside_pct"]), 2) == 16.67
    assert round(float(row["target_change_1m_abs"]), 2) == 5.0
    assert round(float(row["target_change_1m_pct"]), 2) == 5.0
    assert float(row["consensus_delta_1m"]) == 25.0


def test_large_target_cut_forces_committee_review_despite_upside():
    actions = pd.DataFrame([{
        "isin": "FR0000000002",
        "name": "FALLING TARGET",
        "last_close": "70",
        "target_price": "90",
        "consensus_rating": "HOLD",
        "consensus_score": "3",
        "score_brut": "82",
        "comite_status": "COMMITTEE",
    }])
    history = pd.DataFrame([{
        "date": "2026-07-01",
        "isin": "FR0000000002",
        "source": "yfinance",
        "consensus_rating": "BUY",
        "consensus_score_100": "75",
        "n_analysts": "10",
        "strong_buy": None,
        "buy": None,
        "hold": None,
        "sell": None,
        "strong_sell": None,
        "target_low": "90",
        "target_mean": "105",
        "target_high": "120",
        "last_close": "80",
    }], columns=SNAPSHOT_COLUMNS)

    out, _, metrics = enrich_analyst_momentum(
        actions,
        history=history,
        revisions=pd.DataFrame(columns=REVISION_COLUMNS),
        cfg=_cfg(),
        run_date="2026-08-07",
    )
    row = out.iloc[0]
    assert float(row["target_upside_pct"]) > 15.0
    assert float(row["target_change_1m_pct"]) < -10.0
    assert bool(row["committee_review_required"]) is True
    assert row["committee_analyst_gate"] == "BLOCK_NEW_BUY_REVIEW"
    assert metrics["mandatory_reviews"] == 1


def test_broker_weighting_and_revision_breadth():
    actions = pd.DataFrame([{
        "isin": "FR0000000003",
        "last_close": "100",
        "target_price": "120",
        "consensus_rating": "BUY",
        "consensus_score": "4",
        "score_brut": "60",
        "comite_status": "WATCH",
    }])
    revisions = pd.DataFrame([
        {
            "date": "2026-08-01", "isin": "FR0000000003", "broker": "J.P. Morgan",
            "analyst": "A", "old_rating": "HOLD", "new_rating": "BUY",
            "old_target": "100", "new_target": "110", "change_abs": "10",
            "change_pct": "10", "currency": "EUR", "source": "TEST",
        },
        {
            "date": "2026-08-02", "isin": "FR0000000003", "broker": "Other",
            "analyst": "B", "old_rating": "BUY", "new_rating": "BUY",
            "old_target": "120", "new_target": "114", "change_abs": "-6",
            "change_pct": "-5", "currency": "EUR", "source": "TEST",
        },
    ], columns=REVISION_COLUMNS)

    out, _, _ = enrich_analyst_momentum(
        actions,
        revisions=revisions,
        broker_weights={"j.p. morgan": 1.30},
        cfg=_cfg(),
        run_date="2026-08-07",
    )
    row = out.iloc[0]
    assert row["target_raises_30d"] == 1
    assert row["target_cuts_30d"] == 1
    assert row["net_target_revisions_30d"] == 0
    assert abs(float(row["revision_breadth_30d"])) < 0.001
    assert float(row["weighted_target_revision_30d_pct"]) > 0.0


def test_history_is_idempotent_for_same_day_source():
    actions = pd.DataFrame([{
        "isin": "FR0000000004",
        "last_close": "100",
        "target_price": "110",
        "consensus_rating": "BUY",
        "consensus_score": "4",
    }])
    out, first, _ = enrich_analyst_momentum(actions, cfg=_cfg(), run_date="2026-08-07")
    _, second, _ = enrich_analyst_momentum(
        out, history=first, cfg=_cfg(), run_date="2026-08-07"
    )
    assert len(first) == 1
    assert len(second) == 1
