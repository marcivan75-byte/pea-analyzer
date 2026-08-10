import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from src.signals.t1_t2 import check_tct_with_bonus
from src.signals.earnings_proximity import score_earnings_proximity
from src.portfolio.position_sizing import compute_final_position_size
from src.data.demo import generate_demo_signals
from src.pipeline.daily import run_daily_pipeline

def test_earnings_score_bounds():
    s = score_earnings_proximity(days=3, eps_rev=8, beat=75, short=18)
    assert 0 <= s <= 100
    s2 = score_earnings_proximity(days=1, eps_rev=-5, beat=40, short=5)
    assert s2 < s

def test_position_sizing_days1_ignore():
    res = compute_final_position_size(
        setup={"close": 50, "avg_dollar_volume_20d": 5_000_000},
        meta_proba=0.90,
        p_adverse=0.10,
        expected_adverse_gap=-0.02,
        days_to_earnings=1,
        capital=100_000
    )
    assert res["decision"] == "IGNORE"
    assert res["position_pct"] == 0.0

def test_demo_and_pipeline():
    df = generate_demo_signals(n=40, path="data/processed/test_signals.parquet")
    assert len(df) == 40
    result = run_daily_pipeline(df, capital=50_000)
    assert isinstance(result, pd.DataFrame)
    assert "decision" in result.columns
