"""
Tests complets : comité CDC + pipeline + sizing + earnings.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
from src.data.demo import generate_demo_signals
from src.pipeline.daily import run_daily_pipeline
from src.signals.committee import (
    build_committee,
    build_dashboard_secteurs,
    extract_top50_opportunite,
    extract_ultra_earnings_squeeze,
    compute_opportunity_score,
    compute_risk_score,
)
from src.signals.earnings_proximity import score_earnings_proximity
from src.portfolio.position_sizing import compute_final_position_size
from src.signals.t1_t2 import check_tct_with_bonus

def test_earnings_proximity_logic():
    assert score_earnings_proximity(3, 8, 75, 18) >= 90
    assert score_earnings_proximity(1, 8, 75, 18) < score_earnings_proximity(3, 8, 75, 18)
    assert 0 <= score_earnings_proximity(40, -5, 40, 2) <= 100

def test_position_sizing_rules():
    # Days = 1 → toujours IGNORE
    r = compute_final_position_size(
        {"close": 40, "avg_dollar_volume_20d": 5e6},
        meta_proba=0.92, p_adverse=0.10, expected_adverse_gap=-0.02,
        days_to_earnings=1, capital=100_000
    )
    assert r["decision"] == "IGNORE"

    # Haute conviction + faible gap + liquidité OK
    r2 = compute_final_position_size(
        {"close": 40, "avg_dollar_volume_20d": 5e6},
        meta_proba=0.85, p_adverse=0.12, expected_adverse_gap=-0.03,
        days_to_earnings=4, capital=100_000
    )
    assert r2["decision"] == "TAKE"
    assert r2["position_pct"] > 0

def test_committee_columns_present():
    df = generate_demo_signals(n=80, path="data/processed/_test_committee.parquet")
    # Force quelques setups intéressants
    df.loc[0, "setup"] = "T2_CONFIRMATION"
    df.loc[0, "days_to_earnings"] = 3
    df.loc[0, "eps_revision_3m"] = 9
    df.loc[0, "short_interest"] = 18
    df.loc[0, "score_earnings_proximity"] = 92
    df.loc[0, "score_final"] = 72

    res = build_committee(df)
    required = [
        "note_opportunite", "note_risque", "ratio_ro",
        "delai_label", "delai_jours", "action_plan",
        "verdict", "proba_pct", "esperance_pct", "synthese"
    ]
    for col in required:
        assert col in res.columns, f"Colonne comité manquante : {col}"

    ultra = extract_ultra_earnings_squeeze(res)
    top50 = extract_top50_opportunite(res)
    dash = build_dashboard_secteurs(res)

    assert isinstance(ultra, pd.DataFrame)
    assert isinstance(top50, pd.DataFrame)
    assert isinstance(dash, pd.DataFrame)
    assert len(top50) <= 50

def test_full_pipeline_smoke():
    df = generate_demo_signals(n=60, path="data/processed/_test_pipe.parquet")
    result = run_daily_pipeline(df, capital=80_000)
    assert isinstance(result, pd.DataFrame)
    assert "decision" in result.columns
    assert "p_adverse" in result.columns

    committee = build_committee(result)
    assert "note_opportunite" in committee.columns
    assert "verdict" in committee.columns

def test_t1_t2_function_exists_and_safe():
    # DataFrame minimal → ne doit pas crasher
    df = pd.DataFrame({
        "bandwidth": np.random.uniform(0.03, 0.12, 120),
        "close": np.linspace(10, 15, 120),
        "volume": np.random.randint(1000, 5000, 120),
        "bb_high": np.linspace(11, 16, 120),
        "stoch_k": np.random.uniform(20, 80, 120),
        "stoch_d": np.random.uniform(20, 80, 120),
        "macd": np.random.randn(120),
        "macd_signal": np.random.randn(120),
        "rsi": np.random.uniform(30, 70, 120),
        "sar": np.linspace(9, 14, 120),
        "mm50": np.linspace(9.5, 14.5, 120),
    })
    out = check_tct_with_bonus(df, last_T1_bandwidth=0.05)
    assert "bonus" in out and "setup" in out
