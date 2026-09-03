import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v2_forbids_same_close_execution_and_take_profit():
    cfg = json.loads((ROOT / "config/ETF_GROK2_EXIT_RESEARCH_V2.json").read_text(encoding="utf-8"))
    assert cfg["execution"]["entry"] == "NEXT_SESSION_CLOSE"
    assert cfg["execution"]["exit"] == "NEXT_SESSION_CLOSE"
    assert cfg["governance"]["same_close_signal_execution_forbidden"] is True
    assert cfg["governance"]["fixed_take_profit_forbidden"] is True
    assert "target_return" not in json.dumps(cfg).lower()


def test_v2_uses_strict_reversal_and_rotation_hurdles():
    cfg = json.loads((ROOT / "config/ETF_GROK2_EXIT_RESEARCH_V2.json").read_text(encoding="utf-8"))
    assert cfg["review"]["minimum_holding_sessions"] >= 42
    assert cfg["review"]["reversal"]["minimum_confirmations"] == 3
    assert cfg["review"]["score_exhaustion"]["entry_score_drop_points"] >= 10
    assert cfg["review"]["rotation"]["minimum_score_advantage_points"] >= 8
    assert cfg["review"]["rotation"]["minimum_perf63_advantage_points"] >= 0.05
