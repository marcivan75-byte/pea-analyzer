"""Research-only loss-first exit optimisation V2.
Fixed optimized entries. Completed-week exit signals, next-week-open execution only.
Endpoint positions are marked to final completed-week close for evaluation, never treated as executions.
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
from .at_weekly_exit_bench_v1 import ENTRIES, arrays, summarize, _trade

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V2.json'

# The V1 corrected screen showed the strongest defensive family around hard-stop 4.5-5%,
# RSI/stochastic reversal and PSAR, but reward/risk remained about 2.1-2.5 and tail losses
# were still large. V2 focuses on rapid false-positive invalidation and winner preservation.
def C(loss=4.0, early_weeks=None, early_floor=0.0, trail=None, activation=0.0,
      heat='both', trend='psar', rsi=75.0, stoch=75.0):
 return {'loss_cut_pct':loss,'early_weeks':early_weeks,'early_floor_pct':early_floor,
         'trail_pct':trail,'trail_activation_pct':activation,'heat':heat,'trend':trend,
         'rsi_heat':rsi,'stoch_heat':stoch}

CONFIGS=[]
# Loss-first neighbourhood: weekly-close confirmation; no fictitious intrabar fill.
for loss in [2.0,2.5,3.0,3.5,4.0,4.5,5.0,5.5,6.0]:
 for ew in [None,1,2,3]:
  floors=[0.0] if ew is None else [0.0,-1.0,-2.0]
  for floor in floors:
   for heat in ['rsi','both']:
    for rsi in [72.5,75.0,77.5]:
     CONFIGS.append(C(loss=loss,early_weeks=ew,early_floor=floor,heat=heat,trend='psar',rsi=rsi,stoch=75.0))
# Winner-preserving trailing: activation must be reached before trailing is armed.
for loss in [2.5,3.0,3.5,4.0,4.5,5.0]:
 for trail in [4.0,5.0,6.0,7.0]:
  for act in [5.0,8.0,10.0,12.0,15.0]:
   CONFIGS.append(C(loss=loss,trail=trail,activation=act,heat='both',trend='psar',rsi=75.0,stoch=75.0))
# Explicit requested 5% trailing family with early invalidation variants.
for loss in [2.5,3.0,3.5,4.0,4.5,5.0]:
 for ew in [1,2,3]:
  for floor in [0.0,-1.0,-2.0]:
   for act in [5.0,8.0,10.0,12.0]:
    CONFIGS.append(C(loss=loss,early_weeks=ew,early_floor=floor,trail=5.0,activation=act,heat='both',trend='psar',rsi=75.0,stoch=75.0))
_seen=set(); CONFIGS=[x for x in CONFIGS if not ((k:=tuple(sorted(x.items()))) in _seen or _seen.add(k))]


def simulate(sym,b,a,sig,entry_label,cfg,cfg_id):
 o,c,rsi,k,d,psar,s20,s50=(a[x] for x in ['open','close','rsi','k','d','psar','sma20','sma50'])
 pos_idx=-1; price=0.0; peak=0.0; peak_gain=0.0; out=[]
 for i in range(1,len(c)-1):
  nxt=o[i+1]
  if pos_idx>=0 and i>=pos_idx:
   close=c[i]; prev=c[i-1]; peak=max(peak,close); ret=(close/price-1)*100
   peak_gain=max(peak_gain,(peak/price-1)*100); held=i-pos_idx+1; reasons=[]
   loss=cfg['loss_cut_pct']
   if loss is not None and ret<=-loss: reasons.append(f'LOSS_CLOSE_{loss:g}')
   ew=cfg['early_weeks']; floor=cfg['early_floor_pct']
   if ew is not None and held<=ew and ret<=floor and close<prev:
    reasons.append(f'EARLY_FAIL_W{ew}_F{floor:g}')
   t=cfg['trail_pct']; act=cfg['trail_activation_pct']
   if t is not None and peak_gain>=act and (close/peak-1)*100<=-t and close<prev:
    reasons.append(f'TRAIL_{t:g}_ACT{act:g}_REV')
   heat=cfg['heat']; rthr=cfg['rsi_heat']; sthr=cfg['stoch_heat']
   if heat in {'rsi','both'} and np.isfinite(rsi[i]) and rsi[i]>rthr and close<prev:
    reasons.append(f'RSI_GT{rthr:g}_REV')
   crossdn=np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i]<d[i] and k[i-1]>=d[i-1]
   if heat in {'stoch','both'} and np.isfinite(k[i]) and k[i]>sthr and crossdn:
    reasons.append(f'STOCH_GT{sthr:g}_CROSSDOWN')
   if cfg['trend']=='psar' and np.isfinite(psar[i]) and close<psar[i]: reasons.append('CLOSE_LT_PSAR')
   if reasons and np.isfinite(nxt) and nxt>0:
    out.append(_trade(sym,entry_label,cfg_id,b,pos_idx,price,i+1,float(nxt),reasons)); pos_idx=-1; price=0.; peak=0.; peak_gain=0.; continue
  if pos_idx<0 and sig[i] and np.isfinite(nxt) and nxt>0:
   pos_idx=i+1; price=float(nxt); peak=price; peak_gain=0.
 if pos_idx>=0 and np.isfinite(c[-1]) and c[-1]>0:
  out.append(_trade(sym,entry_label,cfg_id,b,pos_idx,price,len(c)-1,float(c[-1]),['ENDPOINT_MARK'],endpoint=True))
 return out


def admissible(by,comb):
 for e in ENTRIES:
  z=by[e['label']]
  if z['ALL']['trades']<50 or z['12M']['trades']<30: return False
  if (z['12M']['profit_factor'] or 0)<1.05 or (z['12M']['mean_return_pct'] or -999)<=0: return False
  if z['ALL']['endpoint_share_pct']>15: return False
 if (comb['12M']['profit_factor'] or 0)<1.10 or (comb['12M']['mean_return_pct'] or -999)<=0: return False
 return True


def robust_values(by,field,default=-999):
 vals=[]
 for e in ENTRIES:
  for w in ['ALL','12M','18M','24M']:
   z=by[e['label']][w]
   if z['trades']>=15:
    v=z.get(field); vals.append(default if v is None else v)
 return vals


def family_best(results,pred):
 xs=[z for z in results if pred(z['config'])]
 return max(xs,key=lambda z:tuple(z['rank_key'])) if xs else None


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
  cid=f'V2_{ci:04d}'; alltr=[]; by={}
  for ent in ENTRIES:
   tr=[]
   for sym,b in bars.items(): tr.extend(simulate(sym,b,arr[sym],signals[sym][ent['label']],ent['label'],cfg,cid))
   by[ent['label']]=summarize(pd.DataFrame(tr),end); alltr.extend(tr)
  comb=summarize(pd.DataFrame(alltr),end); ok=admissible(by,comb)
  rr=min(robust_values(by,'reward_risk',0)); pf=min(robust_values(by,'profit_factor',0)); mean=min(robust_values(by,'mean_return_pct'))
  p10=min(robust_values(by,'p10_return_pct')); avgloss=min(robust_values(by,'avg_loss_pct')); maxloss=min(robust_values(by,'max_loss_pct'))
  # Loss-control objective: admissibility first; then robust R/R, PF, tail and average loss,
  # mean return. This directly reflects the research mandate and prevents a high win-rate baseline
  # with uncontrolled downside from dominating.
  rank=[1 if ok else 0,rr,pf,p10,avgloss,maxloss,mean,comb['ALL']['profit_factor'] or 0,comb['ALL']['trades']]
  results.append({'exit_model':cid,'config':cfg,'admissible':ok,'by_entry':by,'combined':comb,'robust':{'reward_risk':round(rr,3),'profit_factor':round(pf,3),'p10_return_pct':round(p10,3),'avg_loss_pct':round(avgloss,3),'max_loss_pct':round(maxloss,3),'mean_return_pct':round(mean,3)},'rank_key':rank})
 results.sort(key=lambda z:tuple(z['rank_key']),reverse=True)
 best=results[0] if results else None
 diag={
  'best_overall':best,
  'best_5pct_trailing':family_best(results,lambda c:c['trail_pct']==5.0),
  'best_early_invalidation':family_best(results,lambda c:c['early_weeks'] is not None),
  'best_no_trailing':family_best(results,lambda c:c['trail_pct'] is None),
 }
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V2_LOSS_FIRST','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'entries_fixed':ENTRIES,'exit_models_tested':len(CONFIGS),'top_exit_models':results[:25],'diagnostics':diag,'selection_rule':'Fixed entries. Same sample/PF/positive-return endpoint safeguards as corrected V1; rank admissible models by worst reward/risk across both entries and ALL/12M/18M/24M, then PF, P10, average loss, max loss and mean.','requested_5pct_trailing_explicitly_tested':True,'trailing_definition':'Trailing is armed only after a completed-week peak gain reaches activation; exit signal requires completed-week drawdown plus close reversal, execution next-week open.','early_invalidation_definition':'During first 1-3 held completed weeks, negative/near-negative return plus weekly reversal can invalidate; execution next-week open.','endpoint_accounting':'Open endpoint positions marked at final completed-week close for evaluation only.','lookahead_controls':{'signals':'completed_week_only','execution':'next_week_open','intrabar_stop_assumption':False,'endpoint_mark_is_execution':False},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','WEEKLY_CLOSE_CONFIRMED_STOPS_NOT_INTRABAR','ENDPOINT_MARK_TO_MARKET_FOR_CENSORING_CONTROL','RESEARCH_ONLY']}
 OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
