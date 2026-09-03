import json
from pathlib import Path

from v182.backtest.etf_grok2_exit_rotation_research import _reversal

ROOT = Path(__file__).resolve().parents[1]


def test_exit_research_has_no_fixed_take_profit():
    cfg = json.loads((ROOT / "config/ETF_GROK2_EXIT_RESEARCH_V1.json").read_text(encoding="utf-8"))
    text = json.dumps(cfg).lower()
    assert "target_return" not in text
    assert "take_profit" not in text
    assert cfg["portfolio"]["max_positions"] == 2
    assert cfg["objective"]["primary"] == "portfolio_cagr_vs_world"


def test_reversal_requires_confirmations():
    m = {"close": 90.0, "sma50": 100.0, "macd_hist": -1.0, "perf20": 0.02, "perf63": 0.1}
    assert _reversal(m, 2)
    assert not _reversal(m, 3)


def test_variants_cover_rotation_and_trailing_protection():
    cfg = json.loads((ROOT / "config/ETF_GROK2_EXIT_RESEARCH_V1.json").read_text(encoding="utf-8"))
    assert set(cfg["variants"]) == {
        "A_REVERSAL", "B_REVERSAL_SCORE", "C_REVERSAL_SCORE_ROTATION", "D_ROTATION_TRAIL"
    }
    assert cfg["review"]["rotation"]["minimum_score_advantage_points"] > 0
    assert cfg["review"]["trailing_protection"]["drawdown_from_peak"] < 0
