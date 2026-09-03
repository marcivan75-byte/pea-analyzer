import json
from pathlib import Path

from v182.backtest.etf_grok2_world_fallback_research_v3 import _relative_perf63

ROOT = Path(__file__).resolve().parents[1]


def test_v3_governance_and_world_fallback():
    cfg = json.loads((ROOT / "config/ETF_GROK2_EXIT_RESEARCH_V3.json").read_text(encoding="utf-8"))
    assert cfg["portfolio"]["world_fallback_isin"] == "LU1681043599"
    assert cfg["execution"]["entry"] == "NEXT_SESSION_CLOSE"
    assert cfg["execution"]["exit"] == "NEXT_SESSION_CLOSE"
    assert cfg["execution"]["residual_capital"] == "WORLD_FALLBACK"
    assert cfg["governance"]["fixed_take_profit_forbidden"] is True
    assert cfg["governance"]["same_close_signal_execution_forbidden"] is True
    assert cfg["governance"]["score_exhaustion_exit_disabled"] is True
    assert cfg["governance"]["no_real_orders"] is True
    assert cfg["review"]["reversal"]["minimum_confirmations"] == 3
    assert cfg["review"]["rotation"]["minimum_score_advantage_points"] >= 10
    assert cfg["review"]["rotation"]["minimum_perf63_advantage_points"] >= 0.05


def test_relative_perf_helper_is_importable():
    assert callable(_relative_perf63)
