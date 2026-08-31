"""Research-only V11: local refinement around validated V10 Block-E core.
Frozen: entries, stop 9%, FP block, D-01. Only E core thresholds/weights vary.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json,time
import pandas as pd
from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files,_iter_consolidated,CACHE_DIRS,MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import entry_mask
from .at_weekly_exit_bench_v1 import ENTRIES,summarize
from .at_weekly_exit_opt_v8_profit_reversal_blocks import add_indicators,last_daily_change_by_week,robust_score
from .at_weekly_exit_opt_v10_overheat_weight_bank import STOP_PCT,D_CFG,add_e_indicators,simulate,e_metrics,cost_metrics,criterion_trigger_share
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V11_OVERHEAT_CORE_REFINE.json'
PROFILES=[
 {'name':'R78S78','rsi':78.,'stoch':78.,'bb_ext':1.,'sma20_dist':10.,'sma50_dist':15.,'adx':38.,'accel4':12.},
 {'name':'R80S80','rsi':80.,'stoch':80.,'bb_ext':1.,'sma20_dist':10.,'sma50_dist':15.,'adx':38.,'accel4':12.},
 {'name':'R82S82','rsi':82.,'stoch':82.,'bb_ext':1.,'sma20_dist':10.,'sma50_dist':15.,'adx':38.,'accel4':12.},
 {'name':'R80S82','rsi':80.,'stoch':82.,'bb_ext':1.,'sma20_dist':10.,'sma50_dist':15.,'adx':38.,'accel4':12.},
 {'name':'R82S80','rsi':82.,'stoch':80.,'bb_ext':1.,'sma20_dist':10.,'sma50_dist':15.,'adx':38.,'accel4':12.},
]
WEIGHTS=[
 ('W1111',1,1,1,1),('W2111',2,1,1,1),('W1211',1,2,1,1),('W1121',1,1,2,1),('W1112',1,1,1,2),
 ('W2211',2,2,1,1),('W2121',2,1,2,1),('W2112',2,1,1,2),('W1221',1,2,2,1),('W1212',1,2,1,2),
 ('W2221',2,2,2,1),('W2212',2,2,1,2),('W2122',2,1,2,2),('W1222',1,2,2,2),('W2222',2,2,2,2),
 ('W3322',3,3,2,2),('W2233',2,2,3,3),('W3312',3,3,1,2),('W3132',3,1,3,2),('W1332',1,3,3,2),
]
RATIOS=[0.50,0.55,0.60,0.65,0.70,0.75,0.80]

def cfg_for(p,w,ratio):
 name,r,s,h,per=w
 weights={'rsi':r,'stoch':s,'high52':h,'bb':0,'sma20':0,'sma50':0,'adx':0,'accel':0,'persistence':per}
 return {'stage':'V11_LOCAL_CORE','hypothesis':'CORE_RSI_STOCH_52_PERSIST','profile':p,'weights':weights,'weight_scheme':name,'behaviour':'E_CONFIRM_D','score_ratio':ratio}

def run():
 st=time.perf_counter(); bars={}; arr={}; sigs={}; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=add_e_indicators(add_indicators(build_bars(w))); daily=last_daily_change_by_week(hist,b.index)
   bars[sym]=b; first.append(b.index.min()); last.append(b.index.max())
   arr[sym]={'open':b.open.to_numpy(float),'low':b.low.to_numpy(float),'close':b.close.to_numpy(float),'rsi':b.rsi14.to_numpy(float),'k':b.stoch_k.to_numpy(float),'d':b.stoch_d.to_numpy(float),'psar':b.psar.to_numpy(float),'sma20':b.sma20.to_numpy(float),'sma50':b.sma50.to_numpy(float),'bb':b.bb_upper.to_numpy(float),'adx':b.adx.to_numpy(float),'pdi':b.plus_di.to_numpy(float),'mdi':b.minus_di.to_numpy(float),'daily':daily,'h52':b.high52_prev.to_numpy(float),'ret4':b.ret4.to_numpy(float)}
   sigs[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
 end=max(last)
 def ev(mid,cfg=None,control=False):
  combined=[]; by={}
  for e in ENTRIES:
   tr=[]
   for sym,b in bars.items(): tr.extend(simulate(sym,b,arr[sym],sigs[sym][e['label']],e['label'],cfg,mid,control))
   df=pd.DataFrame(tr); by[e['label']]=summarize(df,end); combined.extend(tr)
  df=pd.DataFrame(combined); sm=summarize(df,end); rb=robust_score(sm)
  allm=sm['ALL']; endpoint=allm.get('endpoint_share_pct',100.)
  tail_ok=allm.get('max_loss_pct',-999)>=-10 and rb and rb['min_p10']>=-9.5
  profit_ok=rb and rb['min_pf']>=1.30 and rb['min_mean']>=1.50
  endpoint_ok=endpoint<=3.0
  rr4=bool(rb and rb['min_rr']>=4.0)
  return {'exit_model':mid,'config':cfg,'by_entry':by,'combined':sm,'robust':rb,'e_metrics':e_metrics(df),'cost_sensitivity':cost_metrics(df),'criterion_trigger_counts':criterion_trigger_share(df),'passes_tail_guard':tail_ok,'passes_profit_guard':profit_ok,'passes_endpoint_guard':endpoint_ok,'passes_all_guards':bool(tail_ok and profit_ok and endpoint_ok),'robust_rr4':rr4,'tail_counts':{f'lt_{n}pct':int((df.return_pct<=-n).sum()) for n in [10,15,20]}}
 control=ev('V11_D01_CONTROL',control=True); models=[]; n=0
 for p in PROFILES:
  for w in WEIGHTS:
   for ratio in RATIOS:
    n+=1; z=ev(f'V11_E_{n:04d}',cfg_for(p,w,ratio));
    if z['robust']:
     z['delta_vs_D01']={k:round(z['combined']['ALL'][k]-control['combined']['ALL'][k],3) for k in ['mean_return_pct','profit_factor','reward_risk','p10_return_pct','avg_win_pct','avg_loss_pct','max_loss_pct']}
    models.append(z)
 valid=[z for z in models if z['passes_all_guards'] and z['combined']['ALL']['trades']>=100]
 valid.sort(key=lambda z:(z['robust']['min_rr'],z['robust']['min_pf'],z['robust']['min_mean'],-z['combined']['ALL']['endpoint_share_pct'],z['combined']['ALL']['reward_risk']),reverse=True)
 rr4=[z for z in valid if z['robust_rr4']]
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V11_OVERHEAT_CORE_REFINE','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'locked':{'entries_fixed':True,'protective_stop_pct':STOP_PCT,'false_positive_block_fixed':True,'D01_anchor_fixed':D_CFG},'models_tested':len(models),'eligible_models':len(valid),'robust_rr4_guarded_count':len(rr4),'D01_control':control,'best_guarded':valid[0] if valid else None,'best_rr4_guarded':rr4[0] if rr4 else None,'top30':valid[:30],'lookahead_controls':{'completed_week_signals_only':True,'strategic_execution':'next_week_open','high52_uses_prior_window_only':True,'fixed_stop_known_before_bar':True,'endpoint_mark_is_execution':False},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','RESEARCH_ONLY']}
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n'); print(json.dumps({'status':payload['status'],'models_tested':len(models),'eligible':len(valid),'rr4_guarded':len(rr4),'best':payload['best_guarded']},indent=2)); return payload
if __name__=='__main__': run()
