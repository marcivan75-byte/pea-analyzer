import numpy as np
import pandas as pd

from v182.hebdo.expected_value_ranker import MAE_VALID_STATUS
from v182.hebdo.tabport_meta_component_ablation import StopRiskIsotonic
from v182.hebdo.tabport_meta_walkforward_research import training_cutoff_for_year


def test_holdout_training_cutoff_is_frozen_before_2023():
    c23 = training_cutoff_for_year(2023)
    assert c23 == training_cutoff_for_year(2024) == training_cutoff_for_year(2025) == training_cutoff_for_year(2026)
    assert c23 < pd.Timestamp('2023-01-01', tz='UTC')


def test_stop_isotonic_calibrates_to_valid_probabilities():
    n = 240
    x = np.linspace(0.05, 0.75, n)
    y = x > 0.38
    df = pd.DataFrame({'prob_stop_9': x, 'hit_stop': y})
    model = StopRiskIsotonic()
    result = model.fit(df)
    assert result['status'] == MAE_VALID_STATUS
    scored = model.transform(pd.DataFrame({'prob_stop_9': [0.1, 0.3, 0.6]}))
    p = scored['prob_stop_9'].to_numpy(dtype=float)
    assert np.isfinite(p).all() and ((0 <= p) & (p <= 1)).all()
    assert p[0] <= p[1] <= p[2]
    assert (scored['mae_model_status'] == MAE_VALID_STATUS).all()


def test_stop_isotonic_blocks_tiny_sample():
    df = pd.DataFrame({'prob_stop_9': np.linspace(0.1, 0.9, 20), 'hit_stop': [False, True] * 10})
    result = StopRiskIsotonic().fit(df)
    assert result['status'] == 'BLOCK_STOP_CAL_INSUFFICIENT_ROWS'
