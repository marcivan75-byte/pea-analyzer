import numpy as np
import pandas as pd

from v182.hebdo.expected_value_ranker import EV_VALID_STATUS
from v182.hebdo.tabport_meta_empirical_payoff_research import empirical_payoffs
from v182.hebdo.tabport_meta_walkforward_research import training_cutoff_for_year


def test_holdout_cutoff_frozen_before_2023():
    c23 = training_cutoff_for_year(2023)
    assert c23 == training_cutoff_for_year(2024) == training_cutoff_for_year(2025) == training_cutoff_for_year(2026)
    assert c23 < pd.Timestamp('2023-01-01', tz='UTC')


def test_empirical_payoffs_use_realized_outcomes():
    pnl = np.r_[np.full(60, 0.20), np.full(60, -0.10)]
    r = empirical_payoffs(pd.DataFrame({'outcome_pnl': pnl}))
    assert r['status'] == EV_VALID_STATUS
    assert abs(r['avg_win'] - 0.20) < 1e-12
    assert abs(r['avg_loss'] + 0.10) < 1e-12
    assert abs(r['rr'] - 2.0) < 1e-12


def test_empirical_payoffs_block_small_sample():
    r = empirical_payoffs(pd.DataFrame({'outcome_pnl': [0.2, -0.1] * 20}))
    assert r['status'] == 'BLOCK_PAYOFF_INSUFFICIENT_SAMPLE'
