import pandas as pd
import numpy as np

from v182.hebdo.confirmation_entry import ConfirmationEntry
from v182.hebdo.fp_early_exit import FPEarlyExit
from v182.hebdo.meta_labeler import MetaLabeler
from v182.hebdo.false_positive_filter import FalsePositiveFilter
from v182.hebdo.expected_value_ranker import ExpectedValueRanker
from v182.hebdo.hebdo_at_meta import HebdoATMeta
from v182.scoring.ic_lasso_selector import lasso_select_features
from v182.tct.preopen_enricher import PreopenEnricher


def make_features(n=100):
    return pd.DataFrame({
        'date':pd.date_range('2025-01-01',periods=n,freq='D'),'ticker':[f'T{i}' for i in range(n)],
        'close':np.linspace(20,120,n),'vol_z':np.linspace(-1,3,n),'drawdown_4w':np.linspace(-0.01,-0.10,n),
        'atr_14_pct':np.linspace(0.02,0.05,n),'mom_26w_sector':np.linspace(-1,2,n),'roe':0.10,
        'debt_to_equity':0.8,'sma200':50,'adv_20m_eur':2_000_000,'days_to_earnings':30,'rsi_14_hebdo':55,
        'prob_stop_9':0.30,
    })


class FakePreopen:
    def enrich(self,df,as_of_date):
        out=df.copy(); out['gap_overnight']=0.01; out['preopen_data_status']='OK'; out['score_preopen']=out['EV_net']; return out


def test_meta_pipeline_runs_and_ranks():
    out=HebdoATMeta().run(make_features())
    assert len(out)>0 and out['EV_net'].is_monotonic_decreasing and (out['process_stage']=='RANKED').all()
    assert {'EV_net','tier','prob_stop_9','prob_meta','META_STATUS','selection_confidence','mae_model_status','ev_model_status'}.issubset(out.columns)


def test_unvalidated_components_never_promote_tct():
    out=HebdoATMeta().run(make_features())
    assert not (out['tier']=='TCT').any()
    assert (out['selection_confidence']=='DEGRADED_UNVALIDATED_COMPONENTS').all()
    assert (out['mae_model_status']=='HEURISTIC_UNCALIBRATED').all()
    assert (out['ev_model_status']=='PARAMETRIC_UNCALIBRATED').all()


def test_duplicate_ticker_uses_latest_dated_row():
    df=make_features(2)
    older=df.iloc[[0]].copy(); older['ticker']='ABC'; older['date']='2025-01-01'; older['close']=50
    newer=df.iloc[[1]].copy(); newer['ticker']='ABC'; newer['date']='2025-01-02'; newer['close']=75
    out=HebdoATMeta().run(pd.concat([newer,older],ignore_index=True))
    assert len(out)==1 and float(out.iloc[0]['close'])==75
    assert int(out.iloc[0]['meta_duplicate_rows_resolved'])==1


def test_duplicate_ticker_same_timestamp_blocks():
    df=make_features(2); df['ticker']='ABC'; df['date']='2025-01-01'
    try:
        HebdoATMeta().run(df); assert False
    except ValueError as e:
        assert 'same timestamp' in str(e)


def test_staged_orchestrator_preopen_confirmation_and_exit():
    proc=HebdoATMeta(preopen_enricher=FakePreopen())
    ranked=proc.run(make_features(40)); pre=proc.run_preopen(ranked,'2026-01-05')
    assert not pre.empty and (pre['process_stage']=='PREOPEN_ENRICHED').all()
    first=pre.iloc[[0]].copy(); first['date']='2026-01-02'
    bars=pd.DataFrame([{'ticker':first.iloc[0]['ticker'],'date':'2026-01-05','open':first.iloc[0]['close'],'close':first.iloc[0]['close']*1.01,'vol_z':1}])
    confirmed=proc.run_confirmation_j1(first,bars)
    assert len(confirmed)==1 and bool(confirmed.iloc[0]['enter_confirmed']) is True
    assert confirmed.iloc[0]['confirmation_status']=='CONFIRMED' and confirmed.iloc[0]['process_stage']=='J1_CONFIRMATION'
    hit=proc.check_early_exit(100,{'open':100,'close':97,'low':96.5},2)
    assert hit[0] is True and hit[1].startswith('FAIL_FAST_J2')


def test_default_preopen_has_no_fake_market_data():
    out=PreopenEnricher().enrich(pd.DataFrame({'ticker':['ABC'],'EV_net':[0.04]}),'2026-01-05T08:00:00+01:00')
    assert out.iloc[0]['preopen_data_status']=='BLOCK_DATA_PREOPEN_SOURCE_REQUIRED'
    assert pd.isna(out.iloc[0]['gap_overnight']) and abs(float(out.iloc[0]['score_preopen'])-0.04)<1e-12


def test_preopen_provider_true_gap_does_not_modify_meta_rank():
    def provider(tickers,as_of):
        return pd.DataFrame([{
            'ticker':'ABC','prev_close':100,'prev_close_time':'2026-01-02T17:30:00+01:00',
            'preopen_price':103,'quote_time':'2026-01-05T07:55:00+01:00'
        }])
    out=PreopenEnricher(provider).enrich(pd.DataFrame({'ticker':['ABC'],'EV_net':[0.04]}),'2026-01-05T08:00:00+01:00')
    assert out.iloc[0]['preopen_data_status']=='OK'
    assert abs(out.iloc[0]['gap_overnight']-0.03)<1e-12 and abs(out.iloc[0]['preopen_boost']-0.15)<1e-12
    assert abs(float(out.iloc[0]['score_preopen'])-0.04)<1e-12
    assert out.iloc[0]['preopen_adjustment_status']=='HEURISTIC_UNCALIBRATED_NOT_APPLIED_TO_RANK'


def test_preopen_stale_quote_blocks():
    def provider(tickers,as_of):
        return pd.DataFrame([{
            'ticker':'ABC','prev_close':100,'prev_close_time':'2026-01-02T17:30:00+01:00',
            'preopen_price':103,'quote_time':'2026-01-05T07:00:00+01:00'
        }])
    out=PreopenEnricher(provider,max_quote_age_minutes=30).enrich(pd.DataFrame({'ticker':['ABC'],'EV_net':[0.04]}),'2026-01-05T08:00:00+01:00')
    assert out.iloc[0]['preopen_data_status']=='BLOCK_DATA_PREOPEN_QUOTE'
    assert pd.isna(out.iloc[0]['gap_overnight'])


def test_meta_pipeline_fail_closed_missing_feature():
    try: HebdoATMeta().run(make_features().drop(columns=['atr_14_pct'])); assert False
    except ValueError as e: assert 'BLOCK_DATA_META' in str(e)


def test_meta_pipeline_fail_closed_missing_liquidity():
    try: HebdoATMeta().run(make_features().drop(columns=['adv_20m_eur'])); assert False
    except ValueError as e: assert 'BLOCK_DATA_META' in str(e)


def test_invalid_critical_rows_are_dropped_not_scored():
    df=make_features(20); df.loc[0,'close']=np.nan; out=HebdoATMeta().run(df)
    assert int(out['meta_invalid_rows_dropped'].iloc[0])==1 and 'T0' not in set(out['ticker'])


def test_infinite_critical_values_are_dropped():
    df=make_features(20); df.loc[0,'vol_z']=np.inf; out=HebdoATMeta().run(df)
    assert int(out['meta_invalid_rows_dropped'].iloc[0])==1


def test_meta_pipeline_blocks_if_all_filtered():
    df=make_features(10); df['adv_20m_eur']=1000
    try: HebdoATMeta().run(df); assert False
    except ValueError as e: assert 'fully rejected' in str(e)


def test_optional_numeric_strings_do_not_crash_filter():
    row=pd.Series({'close':'100','sma200':'90','drawdown_4w':'-0.02','atr_14_pct':'0.03','vol_z':'1','days_to_earnings':'30','adv_20m_eur':'2000000','mom_26w_sector':'0','roe':'0.10','debt_to_equity':'0.8'})
    blocked,reason=FalsePositiveFilter().is_loser_certain(row)
    assert blocked is False and reason==''


def test_past_earnings_are_not_future_event_exclusions():
    f=FalsePositiveFilter(); row=pd.Series({'close':100,'sma200':90,'drawdown_4w':-0.02,'atr_14_pct':0.03,'vol_z':1,'days_to_earnings':-2,'adv_20m_eur':2_000_000,'mom_26w_sector':0})
    blocked,reason=f.is_loser_certain(row); assert blocked is False and reason==''


def test_momentum_cannot_override_earnings_or_illiquidity():
    f=FalsePositiveFilter(); df=pd.DataFrame([
        {'ticker':'E','close':100,'sma200':90,'drawdown_4w':0,'atr_14_pct':0.03,'vol_z':1,'days_to_earnings':2,'adv_20m_eur':2_000_000,'mom_26w_sector':3},
        {'ticker':'L','close':100,'sma200':90,'drawdown_4w':0,'atr_14_pct':0.03,'vol_z':1,'days_to_earnings':30,'adv_20m_eur':1000,'mom_26w_sector':3},])
    assert f.filter_batch(df).empty


def test_negative_ev_never_promoted():
    r=ExpectedValueRanker(avg_win=0.01,avg_loss=-0.20,fee=0.02,parameter_status='EMPIRICAL_PURGED_TEMPORAL_OOS')
    df=pd.DataFrame({'prob_meta':[0.1,0.2,0.3],'prob_stop_9':[0.8,0.7,0.6],'mom_26w_sector':[0,0,0],'drawdown_4w':[0,0,0],'vol_z':[0,0,0],
                     'days_to_earnings':[30]*3,'roe':[0.1]*3,'debt_to_equity':[0.8]*3,
                     'meta_model_status':['TRAINED_PURGED_TEMPORAL_OOS']*3,'mae_model_status':['CALIBRATED_TEMPORAL_OOS']*3})
    out=r.rank_batch(df); assert (out['EV_net']<0).all() and (out['tier']=='EXCLU').all()


def test_parametric_ev_blocks_tct_even_if_other_models_valid():
    r=ExpectedValueRanker()
    df=pd.DataFrame({'prob_meta':[0.9,0.8,0.7],'prob_stop_9':[0.1,0.1,0.1],'mom_26w_sector':[1,1,1],'drawdown_4w':[0,0,0],'vol_z':[0,0,0],
                     'days_to_earnings':[30]*3,'roe':[0.1]*3,'debt_to_equity':[0.8]*3,
                     'meta_model_status':['TRAINED_PURGED_TEMPORAL_OOS']*3,'mae_model_status':['CALIBRATED_TEMPORAL_OOS']*3})
    out=r.rank_batch(df); assert not (out['tier']=='TCT').any()


def test_confirmation_requires_dates_even_for_single_bar():
    out=ConfirmationEntry().filter_batch_j1(pd.DataFrame([{'ticker':'ABC','close':100}]),pd.DataFrame([{'ticker':'ABC','open':100,'close':101,'vol_z':1}]))
    assert out.iloc[0]['confirmation_status']=='BLOCK' and out.iloc[0]['confirm_reason']=='BLOCK_DATA_NEXT_BAR_DATE_REQUIRED'


def test_confirmation_reject_is_preserved_for_audit():
    friday=pd.DataFrame([{'ticker':'ABC','close':100,'date':'2026-01-02'}])
    bars=pd.DataFrame([{'ticker':'ABC','date':'2026-01-05','open':98,'close':97,'vol_z':1}])
    out=ConfirmationEntry().filter_batch_j1(friday,bars)
    assert len(out)==1 and out.iloc[0]['confirmation_status']=='REJECT' and bool(out.iloc[0]['enter_confirmed']) is False


def test_confirmation_with_dates_uses_first_bar_after_signal():
    friday=pd.DataFrame([{'ticker':'ABC','close':100,'date':'2026-01-02'}])
    bars=pd.DataFrame([{'ticker':'ABC','date':'2026-01-06','open':100,'close':99,'vol_z':1},{'ticker':'ABC','date':'2026-01-05','open':100,'close':101,'vol_z':1}])
    out=ConfirmationEntry().filter_batch_j1(friday,bars); assert bool(out.iloc[0]['enter_confirmed']) is True


def test_confirmation_blocks_stale_future_bar():
    friday=pd.DataFrame([{'ticker':'ABC','close':100,'date':'2026-01-02'}])
    bars=pd.DataFrame([{'ticker':'ABC','date':'2026-01-12','open':100,'close':101,'vol_z':1}])
    out=ConfirmationEntry().filter_batch_j1(friday,bars); assert out.iloc[0]['confirmation_status']=='BLOCK' and 'STALE' in out.iloc[0]['confirm_reason']


def test_fail_fast_only_below_minus_2_5pct():
    ex=FPEarlyExit(); assert ex.check_exit(100,{'open':100,'close':100.5,'low':100},2)[0] is False
    hit=ex.check_exit(100,{'open':100,'close':97,'low':96.5},2); assert hit[0] is True and hit[1].startswith('FAIL_FAST_J2')


def test_gap_through_stop_uses_open_loss():
    hit=FPEarlyExit().check_exit(100,{'open':85,'close':86,'low':84},1); assert hit[0] is True and abs(hit[2]-(-0.15))<1e-12


def test_trailing_requires_prior_peak_not_current_bar_lookahead():
    ex=FPEarlyExit(); no_prior=ex.check_exit(100,{'open':100.5,'close':104,'low':100.5},5); assert no_prior[0] is False
    active=ex.check_exit(100,{'open':100.5,'close':100.8,'low':100.5,'peak_pnl_prior':0.04},5); assert active[0] is True and active[1].startswith('TRAIL_BE_PRIOR_PEAK')


def test_meta_label_drops_incomplete_outcomes():
    raw=pd.DataFrame({'mfe':[0.10,np.nan],'mae':[-0.02,np.nan],'hit_stop':[False,None],'block_reason':[None,'BLOCK_DATA']})
    out=MetaLabeler(label_horizon_days=2).build_meta_label(raw)
    assert len(out)==1 and int(out.iloc[0]['meta_label'])==1 and int(out.iloc[0]['meta_label_dropped_incomplete'])==1


def test_meta_training_sparse_classes_blocks_safely():
    df=make_features(40); df['meta_label']=1; result=MetaLabeler(label_horizon_days=2).train(df); assert result['status'].startswith('BLOCK_')


def test_meta_training_requires_temporal_evidence():
    df=make_features(80).drop(columns=['date']); df['meta_label']=np.tile([0,1],40)
    try: MetaLabeler(label_horizon_days=2).train(df); assert False
    except ValueError as e: assert 'temporal order evidence missing' in str(e)


def test_meta_training_rejects_nonbinary_labels():
    df=make_features(80); df['meta_label']=np.tile([0,2],40)
    try: MetaLabeler(label_horizon_days=2).train(df); assert False
    except ValueError as e: assert 'binary 0/1' in str(e)


def test_meta_training_uses_calendar_purged_oos_split():
    n=240; df=make_features(n); df['meta_label']=np.tile([0,1],n//2); df['vol_z']=df['meta_label']*3.0
    result=MetaLabeler(label_horizon_days=10).train(df)
    assert result['status']=='TRAINED_PURGED_TEMPORAL_OOS' and result['split_scheme']=='purged_calendar_time_train_cal_test' and result['embargo_days']==10
    assert result['n_train']<n and result['n_test']>0


def test_lasso_requires_datetime_and_horizon():
    X=pd.DataFrame({'f1':np.arange(40,dtype=float)}); y=pd.Series(np.arange(40,dtype=float))
    try: lasso_select_features(X,y,cv=3,label_horizon_days=5); assert False
    except ValueError as e: assert 'DatetimeIndex required' in str(e)
    dates=pd.date_range('2025-01-01',periods=40,freq='D'); X.index=dates; y.index=dates
    try: lasso_select_features(X,y,cv=3); assert False
    except ValueError as e: assert 'label_horizon_days required' in str(e)


def test_lasso_uses_calendar_purge_and_drops_missing_labels():
    n=120; dates=pd.date_range('2025-01-01',periods=n,freq='D')
    X=pd.DataFrame({'f1':np.linspace(-1,1,n),'f2':np.sin(np.linspace(0,8,n))},index=dates)
    y=pd.Series(0.5*X['f1']+0.1*X['f2'],index=dates); y.iloc[-3:]=np.nan
    result=lasso_select_features(X,y,cv=4,label_horizon_days=5)
    assert result['cv_scheme']=='CalendarPurged_Pipeline_GridSearchCV' and result['embargo_days']==5
    assert result['n_labeled']==117 and result['n_dropped_missing_label']==3