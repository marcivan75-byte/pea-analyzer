"""Research-only local backtest around the two retained V3 entry models. No order execution. Exits unchanged."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, random, time
import numpy as np
import pandas as pd
from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files,_iter_consolidated,CACHE_DIRS,MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import FEATURES,RANGES,strength_frame,future_outcomes,diag,full_sim,summarize,max_score

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_V3_LOCAL_OPT.json'
SEED=20260829
TH=[.84,.86,.88,.90,.92,.94]
N=1200
PARENTS={
'CONT_07':{'rsi':5,'stoch':10,'sma20':2,'sma50':4,'psar':4,'ma_trend':15,'close_loc':6,'r1':19,'macd':7,'breakout':13,'br_macd':5,'br_r1':14,'trend_close':3},
'CONT_15':{'rsi':2,'stoch':12,'sma20':6,'sma50':8,'psar':4,'ma_trend':10,'close_loc':8,'r1':6,'macd':14,'breakout':16,'br_macd':4,'br_r1':4,'trend_close':11}}

def perturb(p,r):
 out={}
 for k,v in p.items():
  lo,hi=RANGES[k]; d=5 if k in {'ma_trend','close_loc','r1','macd','breakout','br_macd','br_r1','trend_close'} else 3
  out[k]=max(lo,min(hi,v+r.randint(-d,d)))
 return out

def key(z):
 s=z['full']; a=s['ALL']; vals=[s[k]['win_rate_pct'] for k in ['ALL','12M','18M','24M'] if s[k]['trades']>=15 and s[k]['win_rate_pct'] is not None]
 return (min(vals) if vals else 0,a['win_rate_pct'] or 0,a['p10_return_pct'] if a['p10_return_pct'] is not None else -999,a['profit_factor'] or 0,a['mean_return_pct'] or -999)

def run():
 t=time.perf_counter(); r=random.Random(SEED); OUT.parent.mkdir(parents=True,exist_ok=True); bars={}; rows=[]; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=build_bars(w); bars[sym]=b; first.append(b.index.min()); last.append(b.index.max()); rows.extend(future_outcomes(sym,b,strength_frame(b)))
 ev=pd.DataFrame(rows,columns=['symbol','signal_ns','return_pct','holding_weeks']+FEATURES); X=ev[FEATURES].to_numpy(float); ret=ev.return_pct.to_numpy(float); dates=ev.signal_ns.to_numpy(dtype='datetime64[ns]')
 specs=[]
 for name,p in PARENTS.items():
  bank=[]; seen=set()
  for w in [p]+[perturb(p,r) for _ in range(N)]:
   for th in TH:
    sig=(tuple(w[k] for k in FEATURES),th)
    if sig in seen: continue
    seen.add(sig); d=diag(ret,dates,X,w,th)
    if d: bank.append({'parent':name,'weights':w,'threshold_ratio':th,**d})
  bank.sort(key=lambda z:z['diag_score'],reverse=True); masks=set(); chosen=[]
  for m in bank:
   ww=np.array([m['weights'][k] for k in FEATURES],float); mask=(X@ww)>=m['threshold_ratio']*max_score(m['weights']); sig=np.packbits(mask).tobytes()
   if sig in masks: continue
   masks.add(sig); chosen.append(m)
   if len(chosen)>=8: break
  specs += chosen
 end=max(last); full=[]
 for i,m in enumerate(specs,1):
  tr=[]; label=f"OPT_{m['parent']}_{i:02d}"
  for sym,b in bars.items(): tr.extend(full_sim(sym,b,m,label))
  full.append({'label':label,**m,'full':summarize(pd.DataFrame(tr),end)})
 winners=[]
 for name in PARENTS:
  fam=sorted([z for z in full if z['parent']==name],key=key,reverse=True); eligible=[z for z in fam if z['full']['ALL']['trades']>=50]; winners.append((eligible or fam)[0])
 winners.sort(key=key,reverse=True); full.sort(key=key,reverse=True)
 payload={'status':'SUCCESS','version':'AT_WEEKLY_V3_LOCAL_OPT','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-t,3),'valid_actions':len(bars),'event_rows':len(ev),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'exit_rules_changed':False,'parents':PARENTS,'thresholds':TH,'local_models_per_parent':N,'final_two_models':winners,'top':full,'selection_rule':'One retained model per V3 parent; robust worst win rate across ALL/12M/18M/24M, then ALL win rate, P10, PF and mean return; >=50 trades preferred.','lookahead_controls':{'pivot_r1':'previous_completed_week_only','breakout_highs':'prior_completed_weeks_only','macd':'signal_week_close_only','execution':'next_week_open'},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','RESEARCH_ONLY','NO_EXIT_OPTIMISATION']}
 OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload
if __name__=='__main__': run()
