from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from v182.tct.features_v212 import technical_features
from v182.tct.scoring_v212 import compute_scores,apply_decisions
from v182.tct.backtest_v212 import TradeConfig,make_trade_outcomes,purged_holdout
ROOT=Path(__file__).resolve().parents[1]; CFG=json.loads((ROOT/'data/reference/V21.2_TCT_EXPLOSIF_OPT_CONFIG.json').read_text())
def _base(name='X'):
    return {'isin':name,'pea_confidence':'HIGH','v182_ticker_validation_confidence_pct':99,'liquidity_percentile':.8,'max_drawdown_1y':-20,'volatility_20d':35,'sector_v21':'Tech','coverage_grade_v21':'B_PARTIAL_FORWARD'}
def test_weights_sum_to_one():
    for key in ['pillar_weights','technical_weights','volume_weights','catalyst_weights','analyst_weights']:assert abs(sum(CFG[key].values())-1)<1e-12
def test_gdelt_discovery_never_directly_changes_score():
    a=_base('A'); a.update({'breakout_20d_flag':True,'rsi14':58,'macd_hist':1,'rvol20':2.2,'volume_acceleration_20d':1.4,'relative_strength':80}); b=dict(a); b['isin']='B'; b['gdelt_catalyst_discovery_score']=100; out=compute_scores(pd.DataFrame([a,b]),CFG); assert out.loc[0,'tct_score_v212']==out.loc[1,'tct_score_v212']; assert bool(out.loc[1,'tct_gdelt_attention_flag'])
def test_gap_is_only_volume_not_technical():
    a=_base('A'); a.update({'breakout_20d_flag':True,'rsi14':58,'macd_hist':1,'relative_strength':80,'rvol20':2,'volume_acceleration_20d':1.2,'gap_up_pct':0}); b=dict(a); b['isin']='B'; b['gap_up_pct']=5; out=compute_scores(pd.DataFrame([a,b]),CFG); assert out.loc[0,'tct_technical_impulse_score_v212']==out.loc[1,'tct_technical_impulse_score_v212']; assert out.loc[1,'tct_volume_flow_squeeze_score_v212']>out.loc[0,'tct_volume_flow_squeeze_score_v212']
def test_rumor_alone_cannot_make_core():
    r=_base('R'); r.update({'mna_rumor_score':100,'breakout_20d_flag':False,'rsi14':45,'macd_hist':-1,'relative_strength':20,'rvol20':.8,'volume_acceleration_20d':.8}); out=apply_decisions(compute_scores(pd.DataFrame([r]),CFG),CFG); assert out.loc[0,'tct_catalyst_event_score_v212']<=CFG['gates']['rumor_only_event_cap']; assert out.loc[0,'tct_decision_v212']!='COEUR_TCT_EXPLOSIF'
def test_core_requires_key_coverage():
    r=_base('C'); r.update({'earnings_catalyst_score':100,'guidance_revision_score':100,'major_contract_score':100,'news_catalyst_score':65,'sector_news_score':70,'analyst_momentum_score':100,'broker_weighted_revision_30d':10,'net_upgrades_30d_v21':5,'consensus_delta_4w':8,'valuation_discount_score':100,'action_topdown_score':100,'buyback_score':100,'breakout_20d_flag':True,'rsi14':58,'macd_hist':3,'relative_strength':99,'rvol20':3}); out=apply_decisions(compute_scores(pd.DataFrame([r]),CFG),CFG); assert out.loc[0,'tct_decision_v212']!='COEUR_TCT_EXPLOSIF'
def test_profit_warning_rejects_positive_strategy():
    r=_base('P'); r['profit_warning_flag']=True; r.update({'earnings_catalyst_score':100,'breakout_20d_flag':True,'rsi14':58,'macd_hist':3,'rvol20':3,'volume_acceleration_20d':1.5}); out=apply_decisions(compute_scores(pd.DataFrame([r]),CFG),CFG); assert out.loc[0,'tct_decision_v212']=='REJECT_TCT'
def test_same_bar_target_stop_is_conservative_stop_first():
    dates=pd.date_range('2026-01-01',periods=25,freq='B'); rows=[{'date':d,'instrument_id':'X','open':100,'high':101,'low':99,'close':100} for d in dates]; rows[1].update({'high':116,'low':89,'close':105}); out=make_trade_outcomes(pd.DataFrame(rows),TradeConfig()); assert out.iloc[0].exit_reason=='STOP_SAME_BAR'; assert out.iloc[0].trade_return_pct==-10; assert not bool(out.iloc[0].target_before_stop)
def test_purged_holdout_removes_twenty_snapshots_before_test():
    dates=pd.date_range('2025-01-01',periods=200,freq='B'); rows=[]
    for d in dates:
        for i in range(3):rows.append({'snapshot_date':d,'instrument_id':f'X{i}','score':50+i*20,'target_before_stop':bool(i==2),'trade_return_pct':15 if i==2 else -10,'mfe_pct':20,'mae_pct':-5})
    r=purged_holdout(pd.DataFrame(rows),70,.3,20,min_positive=10,min_train_snapshots=60); assert r['purged_snapshot_count']==20; assert r['train_snapshot_count']+r['purged_snapshot_count']+r['test_snapshot_count']==200
def test_technical_features_expose_advanced_fields():
    n=220; idx=pd.date_range('2025-01-01',periods=n,freq='B'); close=pd.Series(np.linspace(100,160,n),index=idx); h=pd.DataFrame({'Open':close.shift(1).fillna(close.iloc[0]),'High':close*1.01,'Low':close*.99,'Close':close,'Volume':np.linspace(1000,3000,n)},index=idx); f=technical_features(h)
    for key in ['atr_expansion_ratio_v212','bb_width_change5_pct_v212','macd_hist_delta3_v212','volume_trend_5_20_v212','breakout_distance_pct_v212']:assert key in f
