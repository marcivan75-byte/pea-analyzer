from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.decision.ipo_radar_v1 import (
    _standard_candidate,
    classify_candidate,
    deduplicate_candidates,
    evaluate_candidates,
    parse_price_range,
    pea_eligibility_status,
    score_dimension,
)


ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    import json

    return json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))


def test_price_range_and_pea_semantics() -> None:
    low, high, mid = parse_price_range("$14.00 - $16.00")
    assert (low, high, mid) == (14.0, 16.0, 15.0)
    assert pea_eligibility_status("FR") == "POTENTIAL_VERIFY_TAX_AND_SECURITY"
    assert pea_eligibility_status("US") == "LIKELY_INELIGIBLE"
    assert pea_eligibility_status("") == "UNVERIFIED"


def test_available_criteria_are_renormalized_and_coverage_is_reported() -> None:
    score, coverage = score_dimension({"a": 80, "b": None, "c": 40}, {"a": 50, "b": 25, "c": 25})
    assert score == 66.67
    assert coverage == 75.0


def test_deduplication_keeps_cross_source_confirmation() -> None:
    first = _standard_candidate(name="Example SA", symbol="EXM", exchange="NASDAQ", expected_date="2026-09-10", source="FINNHUB")
    second = _standard_candidate(name="Example SA", symbol="EXM", exchange="NASDAQ", expected_date="2026-09-10", source="NASDAQ")
    merged = deduplicate_candidates([first, second], {"NASDAQ": 90, "FINNHUB": 75})
    assert len(merged) == 1
    assert merged[0]["source_count"] == 2
    assert merged[0]["sources"] == "NASDAQ|FINNHUB"


def test_sparse_calendar_data_cannot_create_positive_decision() -> None:
    config = _config()
    candidate = _standard_candidate(
        name="Sparse IPO",
        symbol="SPRS",
        exchange="NASDAQ",
        expected_date="2026-10-01",
        status="expected",
        price_range="$18-$20",
        offer_value=200_000_000,
        source="FINNHUB",
    )
    evaluated = evaluate_candidates([candidate], config, pd.DataFrame())[0]
    assert evaluated["decision"] == "WATCH_DATA_GAP"
    assert evaluated["live_order_allowed"] is False
    assert evaluated["opportunity_coverage_pct"] < config["minimum_scored_weight_pct"]


def test_complete_high_quality_case_can_only_reach_due_diligence() -> None:
    config = _config()
    candidate = _standard_candidate(
        name="Quality IPO",
        symbol="QLTY",
        exchange="EURONEXT",
        expected_date="2026-10-15",
        status="expected",
        price_range="24-25",
        offer_value=750_000_000,
        issuer_country="FR",
        source="EURONEXT",
    )
    for criterion in config["opportunity_weights"]:
        candidate[f"opportunity_{criterion}"] = 85
    for criterion in config["risk_weights"]:
        candidate[f"risk_{criterion}"] = 20
    evaluated = evaluate_candidates([candidate], config, pd.DataFrame())[0]
    assert evaluated["opportunity_score"] >= 80
    assert evaluated["risk_score"] <= 25
    assert evaluated["decision"] == "PRIORITY_DD"
    assert evaluated["live_order_allowed"] is False
    assert config["governance"]["can_create_buy"] is False


def test_hard_block_overrides_good_scores() -> None:
    config = _config()
    row = {
        "status": "expected",
        "hard_flags": "going_concern",
        "opportunity_score": 90,
        "risk_score": 10,
        "net_ipo_score": 90,
        "opportunity_coverage_pct": 100,
        "risk_coverage_pct": 100,
    }
    assert classify_candidate(row, config) == "AVOID_HARD_BLOCK"
