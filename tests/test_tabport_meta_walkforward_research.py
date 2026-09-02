import pandas as pd
from v182.hebdo.tabport_meta_walkforward_research import HOLDOUT_START, training_cutoff_for_year, _weekly_rsi_map


def test_holdout_training_cutoff_is_frozen_before_2023():
    c2023=training_cutoff_for_year(2023)
    assert c2023==HOLDOUT_START-pd.Timedelta(days=182)
    assert training_cutoff_for_year(2024)==c2023
    assert training_cutoff_for_year(2025)==c2023
    assert training_cutoff_for_year(2026)==c2023


def test_development_training_cutoff_expands_without_using_scoring_year():
    assert training_cutoff_for_year(2020)==pd.Timestamp('2020-01-01',tz='UTC')-pd.Timedelta(days=182)
    assert training_cutoff_for_year(2021)>training_cutoff_for_year(2020)
    assert training_cutoff_for_year(2022)>training_cutoff_for_year(2021)


def test_weekly_rsi_is_bounded_when_available():
    dates=pd.date_range('2020-01-01',periods=450,freq='B',tz='UTC')
    technical=pd.DataFrame({'date':dates,'ticker':'AAA','close':[100+i*0.1 for i in range(len(dates))]})
    out=_weekly_rsi_map(technical)
    valid=out['rsi_14_hebdo'].dropna()
    assert len(valid)>0
    assert valid.between(0,100).all()
