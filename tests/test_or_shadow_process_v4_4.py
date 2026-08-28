from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_weekly_runner_keeps_or_shadow_chain():
    text = (ROOT / "src/v182/reporting/weekly_operational_runner_v4_4.py").read_text(encoding="utf-8")
    assert "objectives_risk_shadow_v1" in text
    assert "objectives_risk_challenger_v2" in text
    assert "sector_or_shadow_v1" in text
    assert "ci_challenger_publication_v2" in text
    assert "or_ranking_daily_shadow_v1" in text
    assert "real_orders_enabled" in text
    assert "objectives_risk_reference_influence" in text


def test_publication_emits_dated_hebdo_artifacts():
    text = (ROOT / "src/v182/reporting/ci_challenger_publication_v2.py").read_text(encoding="utf-8")
    assert "OR_RANKING_HEBDO_SHADOW_" in text
    assert "OR_RANKING_HEBDO_SHADOW_ETF_ONLY_" in text
    assert "OR_RANKING_HEBDO_SHADOW_ACTION_CT_ONLY_" in text
    assert "OR_RANKING_ETF_MT_SHADOW_" in text
    assert "OR_HEBDO_FORWARD_VALIDATION_SCHEDULE.csv" in text
    assert '"real_orders_enabled": False' in text or "'real_orders_enabled': False" in text


def test_challenger_formula_is_fifty_thirty_twenty():
    text = (ROOT / "src/v182/reporting/objectives_risk_challenger_v2.py").read_text(encoding="utf-8")
    assert "OR_COMPOSITE_SHADOW" in text
    assert "OR_ENTRY_ACTION_SHADOW" in text
    assert "INSUFFICIENT_ENTRY_PROOF" in text
    assert "NON_ACTIONNABLE_SHADOW" in text
    assert "ATTENDRE_REPLI_SHADOW" in text


def test_daily_adapter_is_fail_closed():
    text = (ROOT / "src/v182/reporting/or_ranking_daily_shadow_v1.py").read_text(encoding="utf-8")
    assert "SKIPPED_NO_DAILY_INPUT" in text
    assert "MIN_HISTORY_SESSIONS = 250" in text
    assert "score_influence" in text
