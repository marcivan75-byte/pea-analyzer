"""Research-only V12: prior-completed-week trailing-reversal exit bench.

Frozen: entry models, standing protective stop 9%, early false-positive block,
and D-01 reversal anchor. Block E is NOT used as an execution trigger in this
bench because V11 endpoint-guarded refinement did not improve D-01 robustly.

Trailing contract: the trailing reference is the highest completed-week close
known BEFORE the current week. A trailing breach is therefore evaluated against
a level fixed before the current completed-week close. Strategic trailing signals
execute next-week open. This avoids same-bar future-high/low ordering bias.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json,time,math
import numpy as np
import pandas as pd
from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files,_iter_consolidated,CACHE_DIRS,MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import entry_mask
from .at_weekly_exit_bench_v1 import ENTRIES,summarize
from .at_weekly_exit_opt_v7_protective_stop import fixed_stop_fill
from .at_weekly_exit_opt_v8_profit_reversal_blocks import add_indicators,last_daily_change_by_week,evidence,fp_confirm,robust_score,trade

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V12_TRAILING_REVERSAL.json'
STOP_PCT=9.0; FP_WEEKS=2; FP_FLOOR=-1.5
D_CFG={'family':['daily_drop','bb_reentry','adx_decay','rsi_rev','stoch_rev','psar_break','sma20_break','sma50_break'],'activation_pct':5.0,'score':1,'daily_drop_pct':4.0}
TRAIL_PCTS=[4.0,5.0,6.0]
ACTIVATIONS=[5.0,8.0,10.0,12.0,15.0,20.0]
REVERSAL_MODES=['PRICE_DOWN','RSI_FALLING','STOCH_CROSSDOWN','MOMENTUM_ANY','D_SIGNAL']
COSTS=[0.0,0.2,0.5]

def reversal_ok(mode,i,a,d_signal):
 c=a['close']; r=a['rsi']; k=a['k']; d=a['d']
 price_down=bool(i>=1 and np.isfinite(c[i]) and np.isfinite(c[i-1]) and c[i]<c[i-1])
 rsi_falling=bool(i>=1 and np.isfinite(r[i]) and np.isfinite(r[i-1]) and r[i]<r[i-1])
 stoch_cross=bool(i>=1 and np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i-1]>=d[i-1] and k[i]<d[i])
 if mode=='PRICE_DOWN': return price_down
 if mode=='RSI_FALLING': return rsi_falling
 if mode=='STOCH_CROSSDOWN': return stoch_cross
 if mode=='MOMENTUM_ANY': return bool(price_down and (rsi_falling or stoch_cross))
 if mode=='D_SIGNAL': return bool(d_signal)
 raise ValueError(mode)

def simulate(sym,b,a,sig,entry_label,cfg,model,control=False):
 o,l,c=a['open'],a['low'],a['close']; pos=-1; price=0.; peak_close=np.nan; peak_ret=0.; out=[]
 for i in range(1,len(c)):
  if pos>=0 and i>=pos:
   sp=price*(1-STOP_PCT/100); fill=fixed_stop_fill(o[i],l[i],sp)
   if fill is not None:
    xp,kind=fill; out.append(trade(sym,entry_label,model,b,pos,price,i,xp,[f'PROTECTIVE_STOP_{STOP_PCT:g}_{kind}'],stop_price=sp,peak_ret=peak_ret)); pos=-1; price=0.; peak_close=np.nan; peak_ret=0.; continue
   if i<len(c)-1:
    ret=(c[i]/price-1)*100; held=i-pos+1; reasons=[]
    if held<=FP_WEEKS and ret<=FP_FLOOR and c[i]<c[i-1] and fp_confirm(i,a): reasons.append('EARLY_FALSE_POSITIVE')
    dev=evidence(i,b,a,D_CFG['daily_drop_pct']); d_hits=[k for k in D_CFG['family'] if dev[k]] if peak_ret>=D_CFG['activation_pct'] else []
    d_signal=len(d_hits)>=D_CFG['score']
    if d_signal: reasons.append('PROFIT_REVERSAL_'+'+'.join(d_hits))
    if not control and np.isfinite(peak_close):
     prior_peak_ret=(peak_close/price-1)*100
     trail_level=peak_close*(1-cfg['trail_pct']/100)
     trail_breach=bool(prior_peak_ret>=cfg['activation_pct'] and np.isfinite(c[i]) and c[i]<=trail_level)
     if trail_breach and reversal_ok(cfg['reversal_mode'],i,a,d_signal):
      reasons.append(f"TRAIL_REV_{cfg['trail_pct']:g}_{cfg['reversal_mode']}")
    if reasons and np.isfinite(o[i+1]) and o[i+1]>0:
     if np.isfinite(c[i]): peak_ret=max(peak_ret,(c[i]/price-1)*100)
     out.append(trade(sym,entry_label,model,b,pos,price,i+1,float(o[i+1]),reasons,signal_close=c[i],peak_ret=peak_ret)); pos=-1; price=0.; peak_close=np.nan; peak_ret=0.; continue
   if np.isfinite(c[i]):
    peak_close=float(c[i]) if not np.isfinite(peak_close) else max(float(peak_close),float(c[i]))
    peak_ret=max(peak_ret,(c[i]/price-1)*100)
  if pos<0 and i<len(c)-1 and sig[i] and np.isfinite(o[i+1]) and o[i+1]>0:
   pos=i+1; price=float(o[i+1]); peak_close=price; peak_ret=0.
 if pos>=0 and np.isfinite(c[-1]) and c[-1]>0:
  if np.isfinite(c[-1]): peak_ret=max(peak_ret,(c[-1]/price-1)*100)
  out.append(trade(sym,entry_label,model,b,pos,price,len(c)-1,float(c[-1]),['ENDPOINT_MARK'],endpoint=True,signal_close=c[-1],peak_ret=peak_ret))
 return out

def trail_metrics(df):
 if df.empty:return {'trail_exits':0}
 t=df[df.exit_reasons.str.contains('TRAIL_REV_',na=False)].copy()
 if t.empty:return {'trail_exits':0,'trail_avg_realised_return':None,'trail_avg_peak_return':None,'trail_avg_giveback':None}
 t['giveback']=pd.to_numeric(t.peak_return_pct,errors='coerce')-pd.to_numeric(t.return_pct,errors='coerce')
 return {'trail_exits':int(len(t)),'trail_avg_realised_return':round(float(t.return_pct.mean()),3),'trail_avg_peak_return':round(float(t.peak_return_pct.mean()),3),'trail_avg_giveback':round(float(t.giveback.mean()),3)}

def cost_metrics(df):
 r=pd.to_numeric(df.return_pct,errors='coerce').dropna() if not df.empty else pd.Series(dtype=float); out={}
 for cost in COSTS:
  q=r-cost; w=q[q>0]; los=q[q<0]; pf=float(w.sum()/(-los.sum())) if len(los) and -los.sum()>0 else None
  out[f'cost_{cost:g}pct']={'mean_return_pct':round(float(q.mean()),3) if len(q) else None,'profit_factor':round(pf,3) if pf is not None and math.isfinite(pf) else None,'win_rate_pct':round(float((q>0).mean()*100),2) if len(q) else None}
 return out

def run():
 st=time.perf_counter(); bars={}; arr={}; sigs={}; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=add_indicators(build_bars(w)); daily=last_daily_change_by_week(hist,b.index)
   bars[sym]=b; first.append(b.index.min()); last.append(b.index.max())
   arr[sym]={'open':b.open.to_numpy(float),'low':b.low.to_numpy(float),'close':b.close.to_numpy(float),'rsi':b.rsi14.to_numpy(float),'k':b.stoch_k.to_numpy(float),'d':b.stoch_d.to_numpy(float),'psar':b.psar.to_numpy(float),'sma20':b.sma20.to_numpy(float),'sma50':b.sma50.to_numpy(float),'bb':b.bb_upper.to_numpy(float),'adx':b.adx.to_numpy(float),'pdi':b.plus_di.to_numpy(float),'mdi':b.minus_di.to_numpy(float),'daily':daily}
   sigs[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
 end=max(last)
 def ev(mid,cfg=None,control=False):
  combined=[]; by={}
  for e in ENTRIES:
   tr=[]
   for sym,b in bars.items(): tr.extend(simulate(sym,b,arr[sym],sigs[sym][e['label']],e['label'],cfg,mid,control))
   df=pd.DataFrame(tr); by[e['label']]=summarize(df,end); combined.extend(tr)
  df=pd.DataFrame(combined); sm=summarize(df,end); rb=robust_score(sm); allm=sm['ALL']; endpoint=allm.get('endpoint_share_pct',100.)
  tail_ok=bool(rb and allm.get('max_loss_pct',-999)>=-10 and rb['min_p10']>=-9.5)
  profit_ok=bool(rb and rb['min_pf']>=1.30 and rb['min_mean']>=1.50)
  endpoint_ok=endpoint<=3.0
  return {'exit_model':mid,'config':cfg,'by_entry':by,'combined':sm,'robust':rb,'trail_metrics':trail_metrics(df),'cost_sensitivity':cost_metrics(df),'passes_tail_guard':tail_ok,'passes_profit_guard':profit_ok,'passes_endpoint_guard':endpoint_ok,'passes_all_guards':bool(tail_ok and profit_ok and endpoint_ok),'robust_rr4':bool(rb and rb['min_rr']>=4.0),'tail_counts':{f'lt_{n}pct':int((df.return_pct<=-n).sum()) for n in [10,15,20]}}
 control=ev('V12_D01_CONTROL',control=True); models=[]; n=0
 for tp in TRAIL_PCTS:
  for act in ACTIVATIONS:
   for mode in REVERSAL_MODES:
    n+=1; cfg={'trail_pct':tp,'activation_pct':act,'reversal_mode':mode,'reference':'prior_completed_week_peak_close','execution':'next_week_open'}; z=ev(f'V12_T_{n:04d}',cfg)
    if z['robust']:
     z['delta_vs_D01']={k:round(z['combined']['ALL'][k]-control['combined']['ALL'][k],3) for k in ['mean_return_pct','profit_factor','reward_risk','p10_return_pct','avg_win_pct','avg_loss_pct','max_loss_pct']}
    models.append(z)
 valid=[z for z in models if z['passes_all_guards'] and z['combined']['ALL']['trades']>=100]
 valid.sort(key=lambda z:(z['robust']['min_rr'],z['robust']['min_pf'],z['robust']['min_mean'],-z['combined']['ALL']['endpoint_share_pct'],z['combined']['ALL']['reward_risk']),reverse=True)
 rr4=[z for z in valid if z['robust_rr4']]
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V12_TRAILING_REVERSAL','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'locked':{'entries_fixed':True,'protective_stop_pct':STOP_PCT,'false_positive_block_fixed':True,'D01_anchor_fixed':D_CFG,'block_e_execution_trigger':False},'trailing_contract':{'reference':'prior_completed_week_peak_close_only','same_week_future_high_not_used':True,'signal':'completed_week_only','execution':'next_week_open','five_pct_explicitly_tested':True},'models_tested':len(models),'eligible_models':len(valid),'robust_rr4_guarded_count':len(rr4),'D01_control':control,'best_guarded':valid[0] if valid else None,'best_5pct_guarded':next((z for z in valid if z['config']['trail_pct']==5.0),None),'best_rr4_guarded':rr4[0] if rr4 else None,'top30':valid[:30],'lookahead_controls':{'completed_week_signals_only':True,'strategic_execution':'next_week_open','trailing_uses_prior_completed_week_peak_only':True,'fixed_stop_known_before_bar':True,'endpoint_mark_is_execution':False},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','RESEARCH_ONLY']}
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n'); print(json.dumps({'status':payload['status'],'models_tested':len(models),'eligible':len(valid),'rr4_guarded':len(rr4),'best':payload['best_guarded'],'best_5pct':payload['best_5pct_guarded']},indent=2)); return payload
if __name__=='__main__': run()
