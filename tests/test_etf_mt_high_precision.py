import json
from pathlib import Path

import pytest

from v182.decision.etf_mt_high_precision import (
    Candidate,
    MarketRegime,
    PositionState,
    exit_decision,
    final_score,
    momo_risk_on,
    select_candidates,
    weighted_raw_score,
)


def _config():
    path = Path("config/V20.8_ETF_MT_HIGH_PRECISION.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_weight_sums_and_scope_guards():
    cfg = _config()
    backtested = sum(v["backtested_weight"] for v in cfg["dynamic_criteria"].values())
    future_dynamic = sum(v["recommended_composite_weight"] for v in cfg["dynamic_criteria"].values())
    future_structural = sum(v["recommended_composite_weight"] for v in cfg["structural_overlay"].values())

    assert abs(backtested - 1.0) < 1e-6
    assert abs(future_dynamic - 0.69) < 1e-6
    assert abs(future_structural - 0.31) < 1e-12
    assert abs(future_dynamic + future_structural - 1.0) < 1e-6
    assert len(cfg["dynamic_criteria"]) == 38
    assert len(cfg["structural_overlay"]) == 5
    assert cfg["score"]["active_decision_core"] == "38_DYNAMIC_PIT_BACKTESTED"
    assert cfg["quality_gates"]["structural_overlay_can_promote_signal"] is False
    assert cfg["scope"]["ct_enabled"] is False
    assert cfg["scope"]["t1_t2_enabled"] is False


def test_weighted_raw_score_requires_all_configured_dynamic_criteria():
    cfg = _config()
    weights = {name: item["backtested_weight"] for name, item in cfg["dynamic_criteria"].items()}
    scores = {name: 80.0 for name in weights}
    assert abs(weighted_raw_score(scores, weights) - 80.0) < 1e-12

    scores.pop(next(iter(weights)))
    with pytest.raises(ValueError, match="missing required MT criteria"):
        weighted_raw_score(scores, weights)


def test_weighted_raw_score_rejects_out_of_range_scores():
    with pytest.raises(ValueError, match="out of 0-100 range"):
        weighted_raw_score({"a": 101.0}, {"a": 1.0})


def test_score_formula_matches_backtest_example():
    score = final_score(91.49338354580647, 100.0)
    assert abs(score - 95.32136095019357) < 1e-12


def test_momo_risk_on_gate_is_strict():
    allowed = MarketRegime(0.50, -0.01, 0.001, True)
    assert momo_risk_on(allowed)
    assert not momo_risk_on(MarketRegime(0.4999, -0.01, 0.001, True))
    assert not momo_risk_on(MarketRegime(0.50, -0.0101, 0.001, True))
    assert not momo_risk_on(MarketRegime(0.50, -0.01, 0.0, True))
    assert not momo_risk_on(MarketRegime(0.50, -0.01, 0.001, False))


def test_selection_prioritises_precision_and_top_two():
    regime = MarketRegime(0.70, 0.01, 0.08, True)
    candidates = [
        Candidate("A", 95, 100, "EUROPE"),
        Candidate("B", 92, 98, "USA"),
        Candidate("C", 91, 97, "TECH"),
    ]
    selected = select_candidates(candidates, regime)
    assert [c.instrument_id for c in selected] == ["A", "B"]


def test_selection_abstains_outside_momo_risk_on():
    regime = MarketRegime(0.49, 0.01, 0.08, True)
    selected = select_candidates([Candidate("A", 100, 100, "EUROPE")], regime)
    assert selected == []


def test_selection_blocks_third_similar_exposure():
    regime = MarketRegime(0.70, 0.01, 0.08, True)
    candidates = [
        Candidate("A", 95, 100, "EUROPE"),
        Candidate("B", 94, 99, "USA"),
    ]
    selected = select_candidates(candidates, regime, active_exposure_groups=["EUROPE", "EUROPE"])
    assert [c.instrument_id for c in selected] == ["B"]


def test_exit_policy_close_only_thresholds():
    assert exit_decision(PositionState(100, 104, 10)).reason == "TARGET_CLOSE"
    assert exit_decision(PositionState(100, 82, 10)).reason == "STOP_CLOSE"
    assert exit_decision(PositionState(100, 101, 168)).reason == "TIME_CLOSE"
    assert exit_decision(PositionState(100, 101, 10)).should_exit is False
