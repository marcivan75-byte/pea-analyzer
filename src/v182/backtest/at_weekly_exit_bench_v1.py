"""Research-only weekly exit test bench for the two retained optimized entry models.
Completed-week signals only; all rule-based exits execute at next-week open. No production impact.
Open positions at the sample endpoint are marked to the final completed-week close for unbiased evaluation.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files,_iter_consolidated,CACHE_DIRS,MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import entry_mask

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_BENCH_V1.json'
ENTRIES=[
 {'label':'OPT_CONT_07_05','weights':{'rsi':6,'stoch':11,'sma20':5,'sma50':6,'psar':3,'ma_trend':18,'close_loc':6,'r1':15,'macd':11,'breakout':12,'br_macd':8,'br_r1':10,'trend_close':8},'threshold_ratio':0.86},
 {'label':'OPT_CONT_15_12','weights':{'rsi':1,'stoch':12,'sma20':6,'sma50':8,'psar':3,'ma_trend':11,'close_loc':6,'r1':5,'macd':12,'breakout':12,'br_macd':4,'br_r1':4,'trend_close':7},'threshold_ratio':0.88},
]

def C(h=None,t=None,heat='none',trend='none',lock=None,baseline=False,rsi=75.0,stoch=75.0):
 return {'hard_stop_pct':h,'trail_pct':t,'heat':heat,'trend':trend,'profit_lock_pct':lock,'baseline':baseline,'rsi_heat':rsi,'stoch_heat':stoch}

# Broad structural screen plus a focused, pre-specified neighbourhood around the robust
# stop + heat + PSAR family found after endpoint-censoring correction.
CONFIGS=[C(baseline=True)]
CONFIGS += [C(h=x) for x in [3,4,5,6,7,9]]
CONFIGS += [C(t=x) for x in [3,4,5,6,7,9]]
CONFIGS += [C(heat=x) for x in ['rsi','stoch','both']]
CONFIGS += [C(trend=x) for x in ['psar','sma20','psar_sma20']]
CONFIGS += [C(h=h,t=t) for h in [4,5,7] for t in [4,5,7]]
CONFIGS += [C(t=t,heat=heat) for t in [4,5,7] for heat in ['rsi','stoch','both']]
CONFIGS += [C(h=h,heat='both',trend='psar') for h in [4,5,6,7]]
CONFIGS += [C(h=h,t=5,heat='both',trend=trend) for h in [4,5,6,7] for trend in ['psar','psar_sma20']]
CONFIGS += [C(h=5,t=5,heat='both',trend='psar',lock=p) for p in [5,8,10,12]]
# Fine local refinement: no entry changes. Keep grid compact to limit over-fitting.
CONFIGS += [C(h=h,heat=heat,trend=trend,rsi=rsi,stoch=stoch)
            for h in [4.0,4.5,5.0,5.5,6.0,6.5]
            for heat in ['rsi','both']
            for trend in ['psar','psar_sma20']
            for rsi in [72.5,75.0,77.5]
            for stoch in [75.0,80.0]]
CONFIGS += [C(h=h,t=t,heat='both',trend='psar',rsi=rsi,stoch=stoch)
            for h in [4.0,5.0,6.0]
            for t in [4.0,5.0,6.0]
            for rsi in [72.5,75.0,77.5]
            for stoch in [75.0,80.0]]
CONFIGS += [C(h=5.0,t=5.0,heat='both',trend='psar',lock=p,rsi=rsi,stoch=stoch)
            for p in [6.0,8.0,10.0,12.0,15.0]
            for rsi in [72.5,75.0,77.5]
            for stoch in [75.0,80.0]]
_seen=set(); CONFIGS=[x for x in CONFIGS if not ((k:=tuple(sorted(x.items()))) in _seen or _seen.add(k))]

def arrays(b):
 return {
  'open':b.open.to_numpy(float),'close':b.close.to_numpy(float),'rsi':b.rsi14.to_numpy(float),
  'k':b.stoch_k.to_numpy(float),'d':b.stoch_d.to_numpy(float),'psar':b.psar.to_numpy(float),
  'sma20':b.sma20.to_numpy(float),'sma50':b.sma50.to_numpy(float),
 }

def _trade(sym,entry_label,cfg_id,b,pos_idx,price,exit_idx,exit_price,reasons,endpoint=False):
 return {'symbol':sym,'entry_model':entry_label,'exit_model':cfg_id,
         'entry_date':b.index[pos_idx].date().isoformat(),'exit_date':b.index[exit_idx].date().isoformat(),
         'return_pct':(exit_price/price-1)*100,'holding_weeks':max(0,exit_idx-pos_idx),
         'exit_reasons':'|'.join(reasons),'endpoint_mark':bool(endpoint)}

def simulate(sym,b,a,sig,entry_label,cfg,cfg_id):
 o,c,rsi,k,d,psar,s20,s50=(a[x] for x in ['open','close','rsi','k','d','psar','sma20','sma50'])
 pos_idx=-1; price=0.0; peak=0.0; out=[]
 for i in range(1,len(c)-1):
  nxt=o[i+1]
  if pos_idx>=0 and i>=pos_idx:
   close=c[i]; prev=c[i-1]; peak=max(peak,close); ret=(close/price-1)*100; reasons=[]
   if cfg['baseline']:
    if np.isfinite(rsi[i]) and rsi[i]>75: reasons.append('RSI_GT75')
    if np.isfinite(k[i]) and k[i]>75: reasons.append('STOCH_GT75')
    if np.isfinite(s20[i]) and close<s20[i]: reasons.append('CLOSE_LT_SMA20')
    if np.isfinite(s50[i]) and close<s50[i]: reasons.append('CLOSE_LT_SMA50')
    if np.isfinite(psar[i]) and close<psar[i]: reasons.append('CLOSE_LT_PSAR')
   else:
    h=cfg['hard_stop_pct']
    if h is not None and ret<=-h: reasons.append(f'HARD_CLOSE_{h}')
    t=cfg['trail_pct']
    if t is not None and peak>price and (close/peak-1)*100<=-t and close<prev: reasons.append(f'TRAIL_{t}_REV')
    heat=cfg['heat']; rthr=float(cfg.get('rsi_heat',75.0)); sthr=float(cfg.get('stoch_heat',75.0))
    if heat in {'rsi','both'} and np.isfinite(rsi[i]) and rsi[i]>rthr and close<prev: reasons.append(f'RSI_GT{rthr:g}_REV')
    crossdn=np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i]<d[i] and k[i-1]>=d[i-1]
    if heat in {'stoch','both'} and np.isfinite(k[i]) and k[i]>sthr and crossdn: reasons.append(f'STOCH_GT{sthr:g}_CROSSDOWN')
    tr=cfg['trend']
    if tr in {'psar','psar_sma20'} and np.isfinite(psar[i]) and close<psar[i]: reasons.append('CLOSE_LT_PSAR')
    if tr in {'sma20','psar_sma20'} and np.isfinite(s20[i]) and close<s20[i]: reasons.append('CLOSE_LT_SMA20')
    lock=cfg['profit_lock_pct']
    if lock is not None and ret>=lock and close<prev and ((np.isfinite(rsi[i]) and rsi[i]>70) or (np.isfinite(k[i]) and k[i]>sthr)):
     reasons.append(f'PROFIT_LOCK_{lock}_REV')
   if reasons and np.isfinite(nxt) and nxt>0:
    out.append(_trade(sym,entry_label,cfg_id,b,pos_idx,price,i+1,float(nxt),reasons))
    pos_idx=-1; price=0.0; peak=0.0; continue
  if pos_idx<0 and sig[i] and np.isfinite(nxt) and nxt>0:
   pos_idx=i+1; price=float(nxt); peak=price
 if pos_idx>=0 and np.isfinite(c[-1]) and c[-1]>0:
  out.append(_trade(sym,entry_label,cfg_id,b,pos_idx,price,len(c)-1,float(c[-1]),['ENDPOINT_MARK'],endpoint=True))
 return out

def metric(df):
 if df.empty: return {'trades':0,'endpoint_marks':0,'endpoint_share_pct':0.0,'win_rate_pct':None,'mean_return_pct':None,'profit_factor':None,'reward_risk':None,'p10_return_pct':None,'avg_loss_pct':None,'avg_win_pct':None,'max_loss_pct':None}
 r=df.return_pct.to_numpy(float); w=r[r>0]; l=r[r<0]; aw=float(w.mean()) if len(w) else 0; al=float(l.mean()) if len(l) else 0
 pf=float(w.sum()/(-l.sum())) if len(l) and -l.sum()>0 else 99.0; rr=float(aw/abs(al)) if al<0 else 99.0
 marks=int(df.endpoint_mark.fillna(False).sum()) if 'endpoint_mark' in df else 0
 return {'trades':len(r),'endpoint_marks':marks,'endpoint_share_pct':round(100*marks/len(r),2),'win_rate_pct':round(float((r>0).mean()*100),2),'mean_return_pct':round(float(r.mean()),3),'profit_factor':round(pf,3),'reward_risk':round(rr,3),'p10_return_pct':round(float(np.quantile(r,.10)),3),'avg_loss_pct':round(al,3),'avg_win_pct':round(aw,3),'max_loss_pct':round(float(r.min()),3)}

def summarize(df,end):
 res={'ALL':metric(df)}; dates=pd.to_datetime(df.entry_date,errors='coerce') if not df.empty else pd.Series(dtype='datetime64[ns]')
 for lab,m in [('12M',12),('18M',18),('24M',24),('36M',36)]: res[lab]=metric(df[dates>=end-pd.DateOffset(months=m)]) if not df.empty else metric(df)
 vals=[res[k] for k in ['ALL','12M','18M','24M'] if res[k]['trades']>=15]
 pf=min((x['profit_factor'] or 0) for x in vals) if vals else 0; rr=min((x['reward_risk'] or 0) for x in vals) if vals else 0
 mean=min((x['mean_return_pct'] if x['mean_return_pct'] is not None else -999) for x in vals) if vals else -999
 p10=min((x['p10_return_pct'] if x['p10_return_pct'] is not None else -999) for x in vals) if vals else -999
 wr=min((x['win_rate_pct'] if x['win_rate_pct'] is not None else 0) for x in vals) if vals else 0
 a=res['ALL']; res['selection_key']=[round(pf,4),round(p10,4),round(mean,4),round(rr,4),round(wr,4),min(a['trades'],250)]
 return res

def admissible(by,comb):
 # Guard against high-R/R false optima: every retained entry must be profitable in 12M,
 # with enough observations; endpoints must not dominate accounting.
 for e in ENTRIES:
  z=by[e['label']]
  if z['ALL']['trades']<50 or z['12M']['trades']<30: return False
  if (z['12M']['profit_factor'] or 0)<1.05 or (z['12M']['mean_return_pct'] or -999)<=0: return False
  if z['ALL']['endpoint_share_pct']>15: return False
 if (comb['12M']['profit_factor'] or 0)<1.10 or (comb['12M']['mean_return_pct'] or -999)<=0: return False
 return True

def run():
 st=time.perf_counter(); OUT.parent.mkdir(parents=True,exist_ok=True); bars={}; arr={}; signals={}; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=build_bars(w); bars[sym]=b; arr[sym]=arrays(b); first.append(b.index.min()); last.append(b.index.max())
   signals[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
 end=max(last); results=[]
 for ci,cfg in enumerate(CONFIGS,1):
  cid=f'EXIT_{ci:03d}'; alltr=[]; by={}
  for ent in ENTRIES:
   tr=[]
   for sym,b in bars.items(): tr.extend(simulate(sym,b,arr[sym],signals[sym][ent['label']],ent['label'],cfg,cid))
   df=pd.DataFrame(tr); by[ent['label']]=summarize(df,end); alltr.extend(tr)
  comb=summarize(pd.DataFrame(alltr),end); ok=admissible(by,comb)
  weak12pf=min(by[e['label']]['12M']['profit_factor'] or 0 for e in ENTRIES)
  weak12p10=min(by[e['label']]['12M']['p10_return_pct'] if by[e['label']]['12M']['p10_return_pct'] is not None else -999 for e in ENTRIES)
  weak12mean=min(by[e['label']]['12M']['mean_return_pct'] if by[e['label']]['12M']['mean_return_pct'] is not None else -999 for e in ENTRIES)
  weak12rr=min(by[e['label']]['12M']['reward_risk'] or 0 for e in ENTRIES)
  weak12wr=min(by[e['label']]['12M']['win_rate_pct'] or 0 for e in ENTRIES)
  endpoint=max(by[e['label']]['ALL']['endpoint_share_pct'] for e in ENTRIES)
  results.append({'exit_model':cid,'config':cfg,'eligible_sample':all(by[e['label']]['ALL']['trades']>=50 for e in ENTRIES),'admissible':ok,'by_entry':by,'combined':comb,
                  'rank_key':[1 if ok else 0,weak12pf,weak12p10,weak12mean,weak12rr,weak12wr,-endpoint,*comb['selection_key']]})
 results.sort(key=lambda z:tuple(z['rank_key']),reverse=True)
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_BENCH_V1_REFINED','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'entries_fixed':ENTRIES,'exit_models_tested':len(CONFIGS),'top_exit_models':results[:20],'best_exit_model':results[0] if results else None,'selection_rule':'Fixed entries. Admissible only if each entry has >=50 ALL and >=30 12M trades, positive 12M mean, 12M PF>=1.05, ALL endpoint share<=15%; combined 12M PF>=1.10. Rank weakest-entry 12M PF, P10, mean, reward/risk, win rate, then endpoint share and combined robustness.','trailing_definition':'Completed-week close drawdown from peak completed-week close plus weekly reversal; next-week-open execution.','endpoint_accounting':'Unresolved positions are included at final completed-week close as ENDPOINT_MARK valuation; this is evaluation accounting, not an executable exit signal.','lookahead_controls':{'signals':'completed_week_only','execution':'next_week_open','intrabar_stop_assumption':False,'endpoint_mark_is_execution':False},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','WEEKLY_CLOSE_CONFIRMED_STOPS_NOT_INTRABAR','ENDPOINT_MARK_TO_MARKET_FOR_CENSORING_CONTROL','RESEARCH_ONLY']}
 OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload
if __name__=='__main__': run()
