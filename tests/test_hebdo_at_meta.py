import pandas as pd
import numpy as np

from v182.hebdo.confirmation_entry import ConfirmationEntry
from v182.hebdo.fp_early_exit import FPEarlyExit
from v182.hebdo.meta_labeler import MetaLabeler
from v182.hebdo.false_positive_filter import FalsePositiveFilter
from v182.hebdo.expected_value_ranker import ExpectedValueRanker
from v182.hebdo.hebdo_at_meta import HebdoATMeta
from v182.scoring.ic_lasso_selector import lasso_select_features


def make_features(n=100):
    return pd.DataFrame({
        'date':pd.date_range('2025-01-01', periods=n, freq='D'),
        'ticker':[f'T{i}' for i in range(n)],
        'close':np.linspace(20,120,n),
        'vol_z':np.linspace(-1,3,n),
        'drawdown_4w':np.linspace(-0.01,-0.10,n),
        'atr_14_pct':np.linspace(0.02,0.05,n),
        'mom_26w_sector':np.linspace(-1,2,n),
        'roe':0.10,
        'debt_to_equity':0.8,
        'sma200':50,
        'adv_20m_eur':2_000_000,
        'days_to_earnings':30,
        'rsi_14_hebdo':55,
    })


class FakePreopen:
    def enrich(self, df, as_of_date):
        out=df.copy()
        out['gap_overnight']=0.01
        out['preopen_data_status']='OK'
        out['score_preopen']=out['EV_net']
        return out


def test_meta_pipeline_runs_and_ranks():
    out=HebdoATMeta().run(make_features())
    assert len(out)>0
    assert {'EV_net','tier','prob_stop_9','prob_meta','META_STATUS','selection_confidence','mae_model_status'}.issubset(out.columns)
    assert out['EV_net'].is_monotonic_decreasing
    assert (out['process_stage']=='RANKED').all()


def test_uncalibrated_models_never_promote_tct():
    out=HebdoATMeta().run(make_features())
    assert not (out['tier']=='TCT').any()
    assert (out['selection_confidence']=='DEGRADED_PARTIAL_OR_UNCALIBRATED_MODELS').all()
    assert (out['mae_model_status']=='HEURISTIC_UNCALIBRATED').all()


def test_staged_orchestrator_preopen_confirmation_and_exit():
    proc=HebdoATMeta(preopen_enricher=FakePreopen())
    ranked=proc.run(make_features(40))
    pre=proc.run_preopen(ranked, '2026-01-05')
    assert not pre.empty
    assert (pre['process_stage']=='PREOPEN_ENRICHED').all()
    first=pre.iloc[[0]].copy()
    first['date']='2026-01-02'
    bars=pd.DataFrame([{'ticker':first.iloc[0]['ticker'],'date':'2026-01-05','open':first.iloc[0]['close'],'close':first.iloc[0]['close']*1.01,'vol_z':1}])
    confirmed=proc.run_confirmation_j1(first,bars)
    assert len(confirmed)==1 and bool(confirmed.iloc[0]['enter_confirmed']) is True
    assert confirmed.iloc[0]['process_stage']=='J1_CONFIRMATION'
    hit=proc.check_early_exit(100, {'open':100,'close':97,'low':96.5}, 2)
    assert hit[0] is True and hit[1].startswith('FAIL_FAST_J2')


def test_meta_pipeline_fail_closed_missing_feature():
    df=make_features().drop(columns=['atr_14_pct'])
    try:
        HebdoATMeta().run(df)
        assert False, 'expected BLOCK_DATA_META'
    except ValueError as e:
        assert 'BLOCK_DATA_META' in str(e)


def test_meta_pipeline_fail_closed_missing_liquidity():
    df=make_features().drop(columns=['adv_20m_eur'])
    try:
        HebdoATMeta().run(df)
        assert False, 'expected BLOCK_DATA_META'
    except ValueError as e:
        assert 'BLOCK_DATA_META' in str(e)


def test_invalid_critical_rows_are_dropped_not_scored():
    df=make_features(20)
    df.loc[0,'close']=np.nan
    out=HebdoATMeta().run(df)
    assert int(out['meta_invalid_rows_dropped'].iloc[0]) == 1
    assert 'T0' not in set(out['ticker'])


def test_infinite_critical_values_are_dropped():
    df=make_features(20)
    df.loc[0,'vol_z']=np.inf
    out=HebdoATMeta().run(df)
    assert int(out['meta_invalid_rows_dropped'].iloc[0]) == 1


def test_meta_pipeline_blocks_if_all_filtered():
    df=make_features(10)
    df['adv_20m_eur']=1000
    try:
        HebdoATMeta().run(df)
        assert False, 'expected fully rejected BLOCK_DATA_META'
    except ValueError as e:
        assert 'fully rejected' in str(e)


def test_past_earnings_are_not_future_event_exclusions():
    f=FalsePositiveFilter()
    row=pd.Series({'close':100,'sma200':90,'drawdown_4w':-0.02,'atr_14_pct':0.03,
                   'vol_z':1,'days_to_earnings':-2,'adv_20m_eur':2_000_000,'mom_26w_sector':0})
    blocked, reason=f.is_loser_certain(row)
    assert blocked is False and reason == ''


def test_momentum_cannot_override_earnings_or_illiquidity():
    f=FalsePositiveFilter()
    df=pd.DataFrame([
        {'ticker':'E','close':100,'sma200':90,'drawdown_4w':0,'atr_14_pct':0.03,'vol_z':1,
         'days_to_earnings':2,'adv_20m_eur':2_000_000,'mom_26w_sector':3},
        {'ticker':'L','close':100,'sma200':90,'drawdown_4w':0,'atr_14_pct':0.03,'vol_z':1,
         'days_to_earnings':30,'adv_20m_eur':1000,'mom_26w_sector':3},
    ])
    out=f.filter_batch(df)
    assert out.empty


def test_negative_ev_never_promoted():
    r=ExpectedValueRanker(avg_win=0.01, avg_loss=-0.20, fee=0.02)
    df=pd.DataFrame({
        'prob_meta':[0.1,0.2,0.3], 'prob_stop_9':[0.8,0.7,0.6],
        'mom_26w_sector':[0,0,0], 'drawdown_4w':[0,0,0], 'vol_z':[0,0,0],
        'meta_model_status':['TRAINED_PURGED_TEMPORAL_OOS']*3,
        'mae_model_status':['CALIBRATED_TEMPORAL_OOS']*3,
    })
    out=r.rank_batch(df)
    assert (out['EV_net']<0).all()
    assert (out['tier']=='EXCLU').all()


def test_confirmation_uses_requested_ticker():
    friday=pd.DataFrame([{'ticker':'ABC','close':100}])
    bars=pd.DataFrame([{'ticker':'ABC','open':100,'close':101,'vol_z':1}])
    out=ConfirmationEntry().filter_batch_j1(friday,bars)
    assert len(out)==1 and bool(out.iloc[0]['enter_confirmed']) is True


def test_confirmation_blocks_ambiguous_duplicate_bars_without_dates():
    friday=pd.DataFrame([{'ticker':'ABC','close':100}])
    bars=pd.DataFrame([
        {'ticker':'ABC','open':100,'close':101,'vol_z':1},
        {'ticker':'ABC','open':101,'close':102,'vol_z':1},
    ])
    out=ConfirmationEntry().filter_batch_j1(friday,bars)
    assert out.iloc[0]['enter_confirmed'] is None
    assert out.iloc[0]['confirm_reason']=='BLOCK_DATA_NEXT_BAR_AMBIGUOUS'


def test_confirmation_with_dates_uses_first_bar_after_signal():
    friday=pd.DataFrame([{'ticker':'ABC','close':100,'date':'2026-01-02'}])
    bars=pd.DataFrame([
        {'ticker':'ABC','date':'2026-01-06','open':100,'close':99,'vol_z':1},
        {'ticker':'ABC','date':'2026-01-05','open':100,'close':101,'vol_z':1},
    ])
    out=ConfirmationEntry().filter_batch_j1(friday,bars)
    assert bool(out.iloc[0]['enter_confirmed']) is True


def test_fail_fast_only_below_minus_2_5pct():
    ex=FPEarlyExit()
    assert ex.check_exit(100, {'open':100,'close':100.5,'low':100}, 2)[0] is False
    hit=ex.check_exit(100, {'open':100,'close':97,'low':96.5}, 2)
    assert hit[0] is True and hit[1].startswith('FAIL_FAST_J2')


def test_gap_through_stop_uses_open_loss():
    ex=FPEarlyExit()
    hit=ex.check_exit(100, {'open':85,'close':86,'low':84}, 1)
    assert hit[0] is True and abs(hit[2] - (-0.15)) < 1e-12


def test_meta_training_sparse_classes_blocks_safely():
    df=make_features(20)
    df['meta_label']=1
    result=MetaLabeler(label_horizon_periods=2).train(df)
    assert result['status'].startswith('BLOCK_')


def test_meta_training_requires_temporal_evidence():
    df=make_features(80).drop(columns=['date'])
    df['meta_label']=np.tile([0,1],40)
    try:
        MetaLabeler(label_horizon_periods=2).train(df)
        assert False, 'expected temporal-order block'
    except ValueError as e:
        assert 'temporal order evidence missing' in str(e)


def test_meta_training_uses_purged_date_grouped_oos_split():
    n=180
    df=make_features(n)
    df['meta_label']=np.tile([0,1], n//2)
    result=MetaLabeler(label_horizon_periods=10).train(df)
    assert result['status']=='TRAINED_PURGED_TEMPORAL_OOS'
    assert result['split_scheme']=='purged_date_grouped_train_cal_test'
    assert result['embargo_periods']==10
    assert result['n_train'] < n and result['n_test'] > 0


def test_lasso_uses_fold_local_scaling_and_drops_missing_labels():
    n=80
    X=pd.DataFrame({'f1':np.linspace(-1,1,n), 'f2':np.sin(np.linspace(0,8,n))})
    y=pd.Series(0.5*X['f1'] + 0.1*X['f2'])
    y.iloc[-3:]=np.nan
    result=lasso_select_features(X,y,cv=4)
    assert result['cv_scheme']=='TimeSeriesSplit_Pipeline_GridSearchCV'
    assert result['n_labeled']==77
    assert result['n_dropped_missing_label']==3
