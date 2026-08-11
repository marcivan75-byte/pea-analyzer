import pytest

from v182.gold.scoring import evaluate_snapshot, load_config, validate_config


def _all_scores(config, value=50.0):
    return {
        criterion["id"]: value
        for family in config["families"].values()
        for criterion in family["criteria"]
    }


def test_gold_v1_config_contract():
    config = load_config()
    validate_config(config)
    assert config["criteria_count"] == 102
    assert config["status"] == "DECISIONAL_SHADOW_RESEARCH_ONLY_PENDING_BACKTEST"
    assert config["decision_policy"] == "ACTIVE_SHADOW"
    assert config["shadow_decision_allowed"] is True
    assert config["real_execution_allowed"] is False
    assert config["t1_t2_policy"] == "EXCLUDED_GOLD; RESERVED_ACTIONS_TCT_ONLY"
    assert sum(f["weight_mt_pct"] for f in config["families"].values()) == pytest.approx(100.0)
    assert sum(f["weight_ct_pct"] for f in config["families"].values()) == pytest.approx(100.0)


def test_neutral_full_coverage_stays_neutral_under_regime_renormalization():
    config = load_config()
    result = evaluate_snapshot(
        {
            "criteria": _all_scores(config, 50.0),
            "qds": 100,
            "regime": "GOLD_MONETARY_BULL",
        },
        config,
    )
    assert result["gold_score_mt"] == pytest.approx(50.0)
    assert result["gold_score_ct"] == pytest.approx(50.0)
    assert result["entry_score"] == pytest.approx(50.0)
    assert result["weight_coverage_mt"] == pytest.approx(1.0)
    assert result["weight_coverage_ct"] == pytest.approx(1.0)
    assert result["decision_mt"] == "NEUTRAL"
    assert result["decision_mode"] == "ACTIVE_SHADOW"
    assert result["shadow_decision_allowed"] is True
    assert result["execution_allowed"] is False
    assert result["real_execution_allowed"] is False
    assert result["execution"] == "RESEARCH_ONLY"
    assert result["backtest_blocks_shadow_decision"] is False
    assert result["t1_t2_used"] is False


def test_high_score_produces_research_decision_before_backtest():
    config = load_config()
    result = evaluate_snapshot(
        {"criteria": _all_scores(config, 90.0), "qds": 100.0}, config
    )
    assert result["decision_mt"] == "CONVICTION_EXCEPTIONAL"
    assert result["shadow_decision_allowed"] is True
    assert result["execution_allowed"] is False
    assert result["backtest_validation_required"] is True
    assert result["backtest_blocks_shadow_decision"] is False


def test_data_quality_gate_blocks_low_qds():
    config = load_config()
    result = evaluate_snapshot(
        {"criteria": _all_scores(config, 90.0), "qds": 74.99}, config
    )
    assert result["decision_mt"] == "NO_DECISION_DATA_QUALITY"
    assert result["confidence"] == "BLOCKED"
    assert result["shadow_decision_allowed"] is True
    assert result["execution_allowed"] is False


def test_reduced_confidence_between_75_and_85():
    config = load_config()
    result = evaluate_snapshot(
        {"criteria": _all_scores(config, 80.0), "qds": 80.0}, config
    )
    assert result["decision_mt"] == "SELECTIVE_BUY"
    assert result["confidence"] == "REDUCED"


def test_anti_look_ahead_gate_is_blocking():
    config = load_config()
    result = evaluate_snapshot(
        {
            "criteria": _all_scores(config, 90.0),
            "qds": 100.0,
            "active_gates": ["ANTI_LOOK_AHEAD"],
        },
        config,
    )
    assert result["decision_mt"] == "NO_DECISION_ANTI_LOOK_AHEAD"
    assert result["confidence"] == "BLOCKED"


def test_no_criteria_means_no_fabricated_score():
    config = load_config()
    result = evaluate_snapshot(
        {"criteria": {}, "qds": 0, "active_gates": ["DATA_QUALITY"]}, config
    )
    assert result["gold_score_mt"] is None
    assert result["gold_score_ct"] is None
    assert result["entry_score"] is None
    assert result["decision_mt"] == "NO_DECISION_DATA_QUALITY"


def test_unknown_criterion_is_ignored_but_unknown_gate_is_rejected():
    config = load_config()
    result = evaluate_snapshot({"criteria": {"ZZ1": 100}, "qds": 0}, config)
    assert result["gold_score_mt"] is None
    with pytest.raises(ValueError):
        evaluate_snapshot(
            {"criteria": {}, "qds": 100, "active_gates": ["INVENTED_GATE"]},
            config,
        )
