"""Research-only V9: Block E overheating selection/weighting bench.

Frozen for this experiment: entries, standing protective stop 9%, false-positive block,
and V8 D-01 reversal anchor (FULL, activation 5%, score 1, daily drop 4%).
Block E is evaluated only for incremental take-profit behaviour.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import itertools,json,time
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
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V9_OVERHEAT_BLOCK_E.json'
STOP_PCT=9.0; FP_WEEKS=2; FP_FLOOR=-1.5
D_CFG={'family':['daily_drop','bb_reentry','adx_decay','rsi_rev','stoch_rev','psar_break','sma20_break','sma50_break'],'activation_pct':5.0,'score':1,'daily_drop_pct':4.0}
PROFILES=[
 {'name':'P75','rsi':75.,'stoch':75.,'bb_ext':1.00,'sma20_dist':8.,'adx':35.},
 {'name':'P80','rsi':80.,'stoch':80.,'bb_ext':1.00,'sma20_dist':10.,'adx':38.},
 {'name':'P85','rsi':85.,'stoch':85.,'bb_ext':1.02,'sma20_dist':12.,'adx':40.},
 {'name':'MIX_A','rsi':80.,'stoch':80.,'bb_ext':1.02,'sma20_dist':8.,'adx':35.},
 {'name':'MIX_B','rsi':80.,'stoch':85.,'bb_ext':1.00,'sma20_dist':12.,'adx':40.},
 {'name':'MIX_C','rsi':85.,'stoch':80.,'bb_ext':1.02,'sma20_dist':10.,'adx':38.},
]
WEIGHTS=[
 {'rsi':1,'stoch':1,'high52':1,'bb':1,'sma20':1,'adx':1},
 {'rsi':2,'stoch':2,'high52':1,'bb':1,'sma20':1,'adx':1},
 {'rsi':1,'stoch':1,'high52':2,'bb':2,'sma20':1,'adx':1},
 {'rsi':2,'stoch':1,'high52':2,'bb':1,'sma20':1,'adx':1},
 {'rsi':1,'stoch':2,'high52':2,'bb':1,'sma20':1,'adx':1},
 {'rsi':2,'stoch':2,'high52':2,'bb':2,'sma20':1,'adx':1},
]
BEHAVIOURS=['D_OR_E_EXTREME','E_CONFIRM_D']
SCORE_RATIOS=[0.45,0.60,0.75]


def add_e_indicators(b):
 x=b.copy(); x['high52_prev']=x.high.shift(1).rolling(52,min_periods=26).max(); return x

def overheat(i,a,p):
 c=a['close']; return {
  'rsi':bool(np.isfinite(a['rsi'][i]) and a['rsi'][i]>=p['rsi']),
  'stoch':bool(np.isfinite(a['k'][i]) and a['k'][i]>=p['stoch']),
  'high52':bool(np.isfinite(a['h52'][i]) and c[i]>a['h52'][i]),
  'bb':bool(np.isfinite(a['bb'][i]) and c[i]>=a['bb'][i]*p['bb_ext']),
  'sma20':bool(np.isfinite(a['sma20'][i]) and a['sma20'][i]>0 and (c[i]/a['sma20'][i]-1)*100>=p['sma20_dist']),
  'adx':bool(np.isfinite(a['adx'][i]) and a['adx'][i]>=p['adx'] and np.isfinite(a['pdi'][i]) and np.isfinite(a['mdi'][i]) and a['pdi'][i]>a['mdi'][i]),
 }

def e_score(ev,w): return sum(w[k] for k,v in ev.items() if v)
def max_score(w): return sum(w.values())

def simulate(sym,b,a,sig,entry_label,cfg,model,control=False):
 o,l,c=a['open'],a['low'],a['close']; pos=-1; price=0.; peak=0.; out=[]
 for i in range(1,len(c)):
  if pos>=0 and i>=pos:
   sp=price*(1-STOP_PCT/100); fill=fixed_stop_fill(o[i],l[i],sp)
   if fill is not None:
    xp,kind=fill; out.append(trade(sym,entry_label,model,b,pos,price,i,xp,[f'PROTECTIVE_STOP_{STOP_PCT:g}_{kind}'],stop_price=sp,peak_ret=peak)); pos=-1; price=0.; peak=0.; continue
   if np.isfinite(c[i]): peak=max(peak,(c[i]/price-1)*100)
   if i<len(c)-1:
    ret=(c[i]/price-1)*100; held=i-pos+1; reasons=[]
    if held<=FP_WEEKS and ret<=FP_FLOOR and c[i]<c[i-1] and fp_confirm(i,a): reasons.append('EARLY_FALSE_POSITIVE')
    d_hits=[]
    if peak>=D_CFG['activation_pct']:
     dev=evidence(i,b,a,D_CFG['daily_drop_pct']); d_hits=[k for k in D_CFG['family'] if dev[k]]
    d_signal=len(d_hits)>=D_CFG['score']
    if control:
     if d_signal: reasons.append('PROFIT_REVERSAL_'+'+'.join(d_hits))
    elif peak>=D_CFG['activation_pct']:
     ee=overheat(i,a,cfg['profile']); sc=e_score(ee,cfg['weights']); need=cfg['score_ratio']*max_score(cfg['weights']); e_signal=sc>=need
     if cfg['behaviour']=='D_OR_E_EXTREME' and (d_signal or e_signal):
      if d_signal: reasons.append('PROFIT_REVERSAL_'+'+'.join(d_hits))
      if e_signal: reasons.append('OVERHEAT_E_'+ '+'.join(k for k,v in ee.items() if v))
     elif cfg['behaviour']=='E_CONFIRM_D' and e_signal and d_signal:
      reasons.append('OVERHEAT_E_CONFIRM_D_'+'+'.join(k for k,v in ee.items() if v))
    if reasons and np.isfinite(o[i+1]) and o[i+1]>0:
     out.append(trade(sym,entry_label,model,b,pos,price,i+1,float(o[i+1]),reasons,signal_close=c[i],peak_ret=peak)); pos=-1; price=0.; peak=0.; continue
  if pos<0 and i<len(c)-1 and sig[i] and np.isfinite(o[i+1]) and o[i+1]>0:
   pos=i+1; price=float(o[i+1]); peak=0.
 if pos>=0 and np.isfinite(c[-1]) and c[-1]>0:
  out.append(trade(sym,entry_label,model,b,pos,price,len(c)-1,float(c[-1]),['ENDPOINT_MARK'],endpoint=True,signal_close=c[-1],peak_ret=peak))
 return out

def extra(df):
 if df.empty:return {}
 e=df[df.exit_reasons.str.contains('OVERHEAT_E',na=False)].copy();
 if e.empty:return {'e_exits':0,'e_avg_realised_return':None,'e_avg_peak_return':None,'e_avg_giveback':None}
 e['giveback']=pd.to_numeric(e.peak_return_pct,errors='coerce')-pd.to_numeric(e.return_pct,errors='coerce')
 return {'e_exits':int(len(e)),'e_avg_realised_return':round(float(e.return_pct.mean()),3),'e_avg_peak_return':round(float(e.peak_return_pct.mean()),3),'e_avg_giveback':round(float(e.giveback.mean()),3)}

def run():
 st=time.perf_counter(); bars={}; arr={}; sigs={}; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=add_e_indicators(add_indicators(build_bars(w))); daily=last_daily_change_by_week(hist,b.index)
   bars[sym]=b; first.append(b.index.min()); last.append(b.index.max())
   arr[sym]={'open':b.open.to_numpy(float),'low':b.low.to_numpy(float),'close':b.close.to_numpy(float),'rsi':b.rsi14.to_numpy(float),'k':b.stoch_k.to_numpy(float),'d':b.stoch_d.to_numpy(float),'psar':b.psar.to_numpy(float),'sma20':b.sma20.to_numpy(float),'sma50':b.sma50.to_numpy(float),'bb':b.bb_upper.to_numpy(float),'adx':b.adx.to_numpy(float),'pdi':b.plus_di.to_numpy(float),'mdi':b.minus_di.to_numpy(float),'daily':daily,'h52':b.high52_prev.to_numpy(float)}
   sigs[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
 end=max(last)
 def eval_model(mid,cfg=None,control=False):
  combined=[]; by={}
  for e in ENTRIES:
   tr=[]
   for sym,b in bars.items(): tr.extend(simulate(sym,b,arr[sym],sigs[sym][e['label']],e['label'],cfg,mid,control))
   df=pd.DataFrame(tr); by[e['label']]=summarize(df,end); combined.extend(tr)
  df=pd.DataFrame(combined); sm=summarize(df,end); rb=robust_score(sm)
  return {'exit_model':mid,'config':cfg,'by_entry':by,'combined':sm,'robust':rb,'e_metrics':extra(df),'tail_counts':{f'lt_{n}pct':int((df.return_pct<=-n).sum()) for n in [10,15,20]},'worst_10':df.sort_values('return_pct').head(10).to_dict('records')}
 control=eval_model('V9_D01_CONTROL',control=True); models=[]; n=0
 for p,w,beh,ratio in itertools.product(PROFILES,WEIGHTS,BEHAVIOURS,SCORE_RATIOS):
  n+=1; cfg={'profile':p,'weights':w,'behaviour':beh,'score_ratio':ratio}; models.append(eval_model(f'V9_E_{n:04d}',cfg))
 valid=[z for z in models if z['robust'] and z['combined']['ALL']['trades']>=100]
 for z in valid:
  z['delta_vs_D01']={k:round(z['combined']['ALL'][k]-control['combined']['ALL'][k],3) for k in ['mean_return_pct','profit_factor','reward_risk','p10_return_pct','avg_win_pct','avg_loss_pct','max_loss_pct'] if z['combined']['ALL'].get(k) is not None and control['combined']['ALL'].get(k) is not None}
 valid.sort(key=lambda z:(z['combined']['ALL']['max_loss_pct'],z['robust']['min_p10'],z['robust']['min_rr'],z['robust']['min_pf'],z['robust']['min_mean']),reverse=True)
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V9_OVERHEAT_BLOCK_E','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'locked':{'entries_fixed':True,'protective_stop_pct':9.0,'false_positive_block_fixed':True,'D01_anchor_fixed':D_CFG},'D01_control':control,'models_tested':len(models),'best_risk_first':valid[0] if valid else None,'top20':valid[:20],'criteria':['RSI overheating','stochastic overheating','fresh prior-window 52-week-high breakout','upper Bollinger extension','distance above SMA20','high ADX with +DI > -DI'],'behaviours':BEHAVIOURS,'lookahead_controls':{'completed_week_signals_only':True,'strategic_execution':'next_week_open','high52_uses_prior_window_only':True,'endpoint_mark_is_execution':False},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','RESEARCH_ONLY']}
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
