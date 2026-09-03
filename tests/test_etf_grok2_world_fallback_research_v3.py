import json
from pathlib import Path

import pandas as pd

from v182.backtest.etf_grok2_world_fallback_research_v3 import _next_common_obs, _relative_perf63

ROOT = Path(__file__).resolve().parents[1]


def test_v3_governance_and_world_fallback():
    cfg = json.loads((ROOT / "config/ETF_GROK2_EXIT_RESEARCH_V3.json").read_text(encoding="utf-8"))
    assert cfg["portfolio"]["world_fallback_isin"] == "LU1681043599"
    assert cfg["portfolio"]["max_grok_positions"] == 2
    assert cfg["portfolio"]["weight_per_grok_position"] == 0.5
    assert cfg["portfolio"]["allocation_model"] == "TWO_INDEPENDENT_50PCT_SLEEVES"
    assert cfg["execution"]["entry"] == "NEXT_SESSION_CLOSE"
    assert cfg["execution"]["exit"] == "NEXT_SESSION_CLOSE"
    assert cfg["execution"]["residual_capital"] == "WORLD_FALLBACK"
    assert cfg["execution"]["cash_target"] == 0.0
    assert cfg["governance"]["fixed_take_profit_forbidden"] is True
    assert cfg["governance"]["same_close_signal_execution_forbidden"] is True
    assert cfg["governance"]["score_exhaustion_exit_disabled"] is True
    assert cfg["governance"]["two_independent_sleeves_required"] is True
    assert cfg["governance"]["cash_reserve_forbidden_in_world_variants"] is True
    assert cfg["governance"]["no_real_orders"] is True
    assert cfg["review"]["reversal"]["minimum_confirmations"] == 3
    assert cfg["review"]["rotation"]["minimum_score_advantage_points"] >= 10
    assert cfg["review"]["rotation"]["minimum_perf63_advantage_points"] >= 0.05


def test_next_common_obs_skips_non_common_session():
    active = pd.DataFrame({"Close": [100.0, 101.0]}, index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    world = pd.DataFrame({"Close": [200.0, 202.0]}, index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    # Signal after the common 2-Jan close: first executable transition must be 5-Jan.
    result = _next_common_obs(active, world, pd.Timestamp("2026-01-02"))
    assert result == (pd.Timestamp("2026-01-05"), 101.0, 202.0)


def test_next_common_obs_never_uses_stale_world_close():
    active = pd.DataFrame({"Close": [100.0, 101.0, 102.0]}, index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]))
    world = pd.DataFrame({"Close": [200.0, 202.0]}, index=pd.to_datetime(["2026-01-02", "2026-01-06"]))
    result = _next_common_obs(active, world, pd.Timestamp("2026-01-02"))
    assert result == (pd.Timestamp("2026-01-06"), 102.0, 202.0)


def test_relative_perf_helper_is_importable():
    assert callable(_relative_perf63)
