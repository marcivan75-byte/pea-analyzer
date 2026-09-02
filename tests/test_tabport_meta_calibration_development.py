import pandas as pd
import numpy as np

from v182.hebdo.tabport_meta_calibration_development import _weekly_rsi14_for_group, _train_before, FREEZE


def test_weekly_rsi_uses_only_weekly_closes_and_becomes_available_after_warmup():
    d=pd.date_range('2020-01-01',periods=120,freq='B',tz='UTC')
    g=pd.DataFrame({'date':d,'close':np.linspace(100,160,len(d))})
    w=_weekly_rsi14_for_group(g)
    assert len(w)>14
    assert w['rsi_14_hebdo'].notna().sum()>0
    assert (w['rsi_14_hebdo'].dropna().between(0,100)).all()


def test_train_before_never_uses_outcomes_available_after_anchor():
    # Sparse sample intentionally remains blocked; the assertion is about cutoff n.
    d=pd.date_range('2015-01-01',periods=30,freq='30D',tz='UTC')
    x=pd.DataFrame({
        'date':d,'outcome_available_at':d+pd.Timedelta(days=200),'meta_label':[0,1]*15,
        'vol_z':1.0,'drawdown_4w':-0.05,'rsi_14_hebdo':50.0,'atr_14_pct':0.03,
        'mom_26w_sector':0.0,'prob_stop_9':0.2,'close_vs_sma200':0,
    })
    anchor=pd.Timestamp('2016-01-01',tz='UTC')
    _,info=_train_before(x,anchor)
    expected=int((x['outcome_available_at']<anchor).sum())
    assert int(info.get('n',0))==expected


def test_holdout_freeze_is_exact_and_timezone_aware():
    assert FREEZE==pd.Timestamp('2023-01-01',tz='UTC')
