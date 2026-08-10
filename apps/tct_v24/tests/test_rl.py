import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from src.ml.reinforcement import RLAgent, build_state_vector, reward_from_outcome, ACTIONS

def test_state_vector_shape():
    row = pd.Series({"meta_proba": 0.7, "setup": "T1"})
    s = build_state_vector(row)
    assert s.shape == (10,)
    assert not np.isnan(s).any()

def test_rl_decide_and_learn(tmp_path: Path):
    agent = RLAgent(model_dir=str(tmp_path / "rl_test"), alpha=0.1, epsilon=0.5)
    row = pd.Series({
        "meta_proba": 0.8, "score_earnings_proximity": 85, "p_adverse": 0.15,
        "days_to_earnings": 3, "vol_ratio": 2.0, "score_final": 70,
        "note_opportunite": 7, "setup": "T2_CONFIRMATION", "short_interest": 12,
        "decision": "TAKE", "position_pct": 0.01, "isin": "TEST", "outcome": 1
    })
    d = agent.decide(row, explore=False)
    assert d["rl_action"] in ACTIONS
    assert 0 <= d["rl_mult"] <= 1.25
    n = agent.learn_from_dataframe(pd.DataFrame([row]))
    assert n == 1

def test_reward_shaping():
    assert reward_from_outcome(1, 2) > 0
    assert reward_from_outcome(0, 2) < 0
    assert reward_from_outcome(0, 0) > 0  # good to ignore a loser
