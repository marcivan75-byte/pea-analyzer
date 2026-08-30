"""Research-only V7: realistic fixed protective-stop audit around the best V6 exit family.
Entries remain fixed. Strategic weekly exits remain completed-week -> next-week open.
Protective stop is known from entry: if a weekly open gaps through it, fill at that open;
otherwise if the weekly low touches it, fill at the stop level. No intrabar trailing assumptions.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json,time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files,_iter_consolidated,CACHE_DIRS,MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import entry_mask
from .at_weekly_exit_bench_v1 import ENTRIES, summarize

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V7_PROTECTIVE_STOP.json'
ANCHOR={'loss_cut_pct':4.5,'fp_weeks':2,'fp_floor_pct':-1.5,'confirm_mode':'momentum_or_trend','rsi_heat':75.0,'stoch_heat':75.0,'trend':'psar'}
STOPS=[None,5.0,7.0,9.0,12.0]

def arrs(b):
 return {k:getattr(b,k).to_numpy(float) for k in ['open','high','low','close']} | {
  'rsi':b.rsi14.to_numpy(float),'k':b.stoch_k.to_numpy(float),'d':b.stoch_d.to_numpy(float),
  'psar':b.psar.to_numpy(float),'sma20':b.sma20.to_numpy(float),'sma50':b.sma50.to_numpy(float)}

def comps(i,a):
 c,rsi,k,d,psar,s20,s50=(a[x] for x in ['close','rsi','k','d','psar','sma20','sma50'])
 return {
  'stoch_crossdown':bool(np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i]<d[i] and k[i-1]>=d[i-1]),
  'rsi_falling':bool(np.isfinite(rsi[i]) and np.isfinite(rsi[i-1]) and rsi[i]<rsi[i-1]),
  'rsi_weak':bool(np.isfinite(rsi[i]) and rsi[i]<50),
  'below_psar':bool(np.isfinite(psar[i]) and c[i]<psar[i]),
  'below_sma20':bool(np.isfinite(s20[i]) and c[i]<s20[i]),
  'below_sma50':bool(np.isfinite(s50[i]) and c[i]<s50[i]),
  'ma20_weakening':bool(i>=2 and np.isfinite(s20[i]) and np.isfinite(s20[i-1]) and s20[i]<s20[i-1]),}

def confirmed(x):
 return x['stoch_crossdown'] or x['rsi_falling'] or x['rsi_weak'] or x['below_psar'] or x['below_sma20'] or x['ma20_weakening']

def trade(sym,entry_label,model,b,ei,ep,xi,xp,reasons,endpoint=False,signal_close=None,stop_price=None):
 return {'symbol':sym,'entry_model':entry_label,'exit_model':model,'entry_date':b.index[ei].date().isoformat(),
  'exit_date':b.index[xi].date().isoformat(),'entry_price':round(float(ep),6),'exit_price':round(float(xp),6),
  'return_pct':(float(xp)/float(ep)-1)*100,'holding_weeks':max(0,xi-ei),'exit_reasons':'|'.join(reasons),
  'endpoint_mark':bool(endpoint),'signal_close':None if signal_close is None else round(float(signal_close),6),
  'stop_price':None if stop_price is None else round(float(stop_price),6)}

def simulate(sym,b,a,sig,entry_label,stop_pct,model):
 o,h,l,c,rsi,k,d,psar=(a[x] for x in ['open','high','low','close','rsi','k','d','psar'])
 pos=-1; price=0.; out=[]
 for i in range(1,len(c)):
  # Position is active for the whole current week, including entry week.
  if pos>=0 and i>=pos:
   if stop_pct is not None:
    sp=price*(1-stop_pct/100.)
    if np.isfinite(o[i]) and o[i]<=sp:
     out.append(trade(sym,entry_label,model,b,pos,price,i,float(o[i]),[f'PROTECTIVE_STOP_{stop_pct:g}_GAP_OPEN'],stop_price=sp)); pos=-1; price=0.; continue
    if np.isfinite(l[i]) and l[i]<=sp:
     out.append(trade(sym,entry_label,model,b,pos,price,i,float(sp),[f'PROTECTIVE_STOP_{stop_pct:g}_TOUCH'],stop_price=sp)); pos=-1; price=0.; continue
   if i < len(c)-1:
    prev=c[i-1]; ret=(c[i]/price-1)*100; held=i-pos+1; reasons=[]
    if ret<=-ANCHOR['loss_cut_pct']: reasons.append(f'LOSS_CLOSE_{ANCHOR["loss_cut_pct"]:g}')
    if held<=ANCHOR['fp_weeks'] and ret<=ANCHOR['fp_floor_pct'] and np.isfinite(prev) and c[i]<prev and confirmed(comps(i,a)):
     reasons.append('CONFIRMED_FALSE_POSITIVE_momentum_or_trend')
    if np.isfinite(rsi[i]) and rsi[i]>ANCHOR['rsi_heat'] and c[i]<prev: reasons.append('RSI_GT75_REV')
    crossdn=np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i]<d[i] and k[i-1]>=d[i-1]
    if np.isfinite(k[i]) and k[i]>ANCHOR['stoch_heat'] and crossdn: reasons.append('STOCH_GT75_CROSSDOWN')
    if np.isfinite(psar[i]) and c[i]<psar[i]: reasons.append('CLOSE_LT_PSAR')
    if reasons and np.isfinite(o[i+1]) and o[i+1]>0:
     out.append(trade(sym,entry_label,model,b,pos,price,i+1,float(o[i+1]),reasons,signal_close=c[i])); pos=-1; price=0.; continue
  if pos<0 and i < len(c)-1 and sig[i] and np.isfinite(o[i+1]) and o[i+1]>0:
   pos=i+1; price=float(o[i+1])
 if pos>=0 and np.isfinite(c[-1]) and c[-1]>0:
  out.append(trade(sym,entry_label,model,b,pos,price,len(c)-1,float(c[-1]),['ENDPOINT_MARK'],endpoint=True,signal_close=c[-1]))
 return out

def run():
 st=time.perf_counter(); bars={}; aa={}; sigs={}; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=build_bars(w)
   if not all(x in b.columns for x in ['open','high','low','close']): continue
   bars[sym]=b; aa[sym]=arrs(b); first.append(b.index.min()); last.append(b.index.max())
   sigs[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
 end=max(last); results=[]; all_trade_sets={}
 for sp in STOPS:
  mid='V7_ANCHOR_NO_INTRAWEEK_STOP' if sp is None else f'V7_PROTECT_{sp:g}'
  by={}; combined=[]
  for e in ENTRIES:
   tr=[]
   for sym,b in bars.items(): tr.extend(simulate(sym,b,aa[sym],sigs[sym][e['label']],e['label'],sp,mid))
   by[e['label']]=summarize(pd.DataFrame(tr),end); combined.extend(tr)
  df=pd.DataFrame(combined); all_trade_sets[mid]=combined
  results.append({'exit_model':mid,'protective_stop_pct':sp,'by_entry':by,'combined':summarize(df,end),
   'tail_counts':{f'lt_{x}pct':int((df.return_pct<=-x).sum()) for x in [10,15,20,30,40]},
   'worst_15_trades':df.sort_values('return_pct').head(15).to_dict('records')})
 # Compare each stop directly with anchor at aggregate level; entries/signals unchanged but exits can permit later re-entry.
 anchor=next(x for x in results if x['protective_stop_pct'] is None)
 for z in results:
  z['delta_vs_anchor_ALL']={k:None if z['combined']['ALL'].get(k) is None or anchor['combined']['ALL'].get(k) is None else round(z['combined']['ALL'][k]-anchor['combined']['ALL'][k],3)
   for k in ['win_rate_pct','mean_return_pct','profit_factor','reward_risk','p10_return_pct','avg_loss_pct','avg_win_pct','max_loss_pct']}
 # Rank risk-first: max loss, P10, then PF and mean; no claim that max loss is guaranteed because gap-through risk remains.
 protected=[z for z in results if z['protective_stop_pct'] is not None]
 protected.sort(key=lambda z:(z['combined']['ALL']['max_loss_pct'],z['combined']['ALL']['p10_return_pct'],z['combined']['ALL']['profit_factor'],z['combined']['ALL']['mean_return_pct']),reverse=True)
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V7_PROTECTIVE_STOP','generated_at_utc':datetime.now(timezone.utc).isoformat(),
  'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},
  'entries_fixed':ENTRIES,'anchor_v6_config':ANCHOR,'models':results,'best_protected_risk_first':protected[0] if protected else None,
  'stop_execution':'Fixed stop known at entry. Weekly open below stop => fill at actual weekly open (gap-through). Else weekly low <= stop => fill at stop. Strategic exits remain completed-week signal -> next-week open.',
  'lookahead_controls':{'entry_models_fixed':True,'strategic_signals':'completed_week_only','strategic_execution':'next_week_open','fixed_stop_known_before_bar':True,'gap_fill':'actual_week_open','intrabar_trailing_assumption':False,'endpoint_mark_is_execution':False},
  'limitations':['WEEKLY_OHLC_CANNOT_RECONSTRUCT_INTRAWEEK_PATH','FIXED_STOP_TOUCH_FILL_ASSUMES_MARKETABLE_STOP_AT_TRIGGER','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','RESEARCH_ONLY']}
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
 print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
