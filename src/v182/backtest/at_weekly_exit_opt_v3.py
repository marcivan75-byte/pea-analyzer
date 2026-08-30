"""Research-only weekly exit optimisation V3.

Fixed optimized entries from V3 local entry optimisation.  This phase changes exits only.
Every executable exit condition is evaluated on a completed weekly bar and executes at the
next weekly open.  Endpoint marks are valuation-only and never executable exits.

V3 refines the validated V2 loss-first families and adds an independent entry-integrity
(decay) exit: if the fixed entry model's current continuous strength has materially decayed
and price confirms a weekly reversal, the position can be invalidated early.  This is meant
to cut false positives before a large loss while preserving winners through separate trailing
and overheat/reversal logic.
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
from .at_weekly_weight_bank_v3_continuous import entry_mask, strength_frame, max_score
from .at_weekly_exit_bench_v1 import ENTRIES, arrays, summarize, _trade

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V3.json'


def C(loss=5.0, early_weeks=2, early_floor=-1.0, decay=None, decay_weeks=3,
      trail=None, activation=12.0, heat='rsi', trend='psar', rsi=75.0, stoch=75.0):
 return {'loss_cut_pct':loss,'early_weeks':early_weeks,'early_floor_pct':early_floor,
         'decay_ratio':decay,'decay_weeks':decay_weeks,'trail_pct':trail,
         'trail_activation_pct':activation,'heat':heat,'trend':trend,
         'rsi_heat':rsi,'stoch_heat':stoch}

CONFIGS=[]
# V2 anchors retained verbatim for an apples-to-apples benchmark.
CONFIGS += [
 C(loss=5.5,early_weeks=2,early_floor=-1.0,decay=None,trail=None,heat='rsi',rsi=75.0),
 C(loss=4.5,early_weeks=2,early_floor=-2.0,decay=None,trail=None,heat='rsi',rsi=75.0),
 C(loss=5.0,early_weeks=2,early_floor=-2.0,decay=None,trail=None,heat='rsi',rsi=75.0),
 C(loss=3.0,early_weeks=2,early_floor=-2.0,decay=None,trail=5.0,activation=5.0,heat='both',rsi=75.0),
]
# Local loss-first + early strength-decay invalidation.
for loss in [4.0,4.5,5.0,5.5,6.0]:
 for floor in [-2.0,-1.0,0.0]:
  for decay in [0.60,0.70,0.80]:
   for dw in [2,3]:
    for rsi in [72.5,75.0]:
     CONFIGS.append(C(loss=loss,early_weeks=2,early_floor=floor,decay=decay,decay_weeks=dw,trail=None,heat='rsi',rsi=rsi))
# Exact requested 5% trailing, armed only after a prior gain, combined with restrained decay.
for loss in [3.0,4.0,5.0]:
 for floor in [-2.0,-1.0]:
  for decay in [0.65,0.75]:
   for act in [8.0,12.0,16.0]:
    for heat in ['rsi','both']:
     CONFIGS.append(C(loss=loss,early_weeks=2,early_floor=floor,decay=decay,decay_weeks=3,trail=5.0,activation=act,heat=heat,rsi=75.0,stoch=75.0))
# Slower integrity decay: lets winners mature while still providing an independent invalidation path.
for loss in [4.5,5.0,5.5]:
 for decay in [0.60,0.70,0.80]:
  for rsi in [75.0,77.5,80.0]:
   CONFIGS.append(C(loss=loss,early_weeks=None,early_floor=0.0,decay=decay,decay_weeks=None,trail=None,heat='rsi',rsi=rsi))
   CONFIGS.append(C(loss=loss,early_weeks=None,early_floor=0.0,decay=decay,decay_weeks=None,trail=5.0,activation=12.0,heat='rsi',rsi=rsi))
_seen=set(); CONFIGS=[x for x in CONFIGS if not ((k:=tuple(sorted(x.items()))) in _seen or _seen.add(k))]


def simulate(sym,b,a,sig,strength_ratio,entry_label,cfg,cfg_id):
 o,c,rsi,k,d,psar=(a[x] for x in ['open','close','rsi','k','d','psar'])
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
   decay=cfg['decay_ratio']; dw=cfg['decay_weeks']
   if decay is not None and (dw is None or held<=dw) and np.isfinite(strength_ratio[i]) and strength_ratio[i]<=decay and close<prev:
    reasons.append(f'STRENGTH_DECAY_{decay:.2f}')
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


def best_family(results,pred):
 xs=[z for z in results if pred(z['config']) and z['admissible']]
 return max(xs,key=lambda z:tuple(z['rank_key'])) if xs else None


def run():
 st=time.perf_counter(); OUT.parent.mkdir(parents=True,exist_ok=True)
 bars={}; arr={}; signals={}; strengths={}; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=build_bars(w); bars[sym]=b; arr[sym]=arrays(b); first.append(b.index.min()); last.append(b.index.max())
   sf=strength_frame(b)
   signals[sym]={}; strengths[sym]={}
   for e in ENTRIES:
    signals[sym][e['label']]=entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool)
    ww=np.array([e['weights'][k] for k in sf.columns],dtype=float)
    strengths[sym][e['label']]=(sf.to_numpy(float)@ww/max_score(e['weights']))
 end=max(last); results=[]
 for ci,cfg in enumerate(CONFIGS,1):
  cid=f'V3_{ci:04d}'; alltr=[]; by={}
  for ent in ENTRIES:
   tr=[]; lab=ent['label']
   for sym,b in bars.items():
    tr.extend(simulate(sym,b,arr[sym],signals[sym][lab],strengths[sym][lab],lab,cfg,cid))
   by[lab]=summarize(pd.DataFrame(tr),end); alltr.extend(tr)
  comb=summarize(pd.DataFrame(alltr),end); ok=admissible(by,comb)
  rr=min(robust_values(by,'reward_risk',0)); pf=min(robust_values(by,'profit_factor',0)); mean=min(robust_values(by,'mean_return_pct'))
  p10=min(robust_values(by,'p10_return_pct')); avgloss=min(robust_values(by,'avg_loss_pct')); maxloss=min(robust_values(by,'max_loss_pct'))
  wr=min(robust_values(by,'win_rate_pct',0))
  # Robust R/R remains primary mandate; PF and loss tails break ties.  Win rate is recorded
  # as a winner-preservation diagnostic, not optimized ahead of loss control.
  rank=[1 if ok else 0,rr,pf,p10,avgloss,maxloss,mean,wr,comb['ALL']['profit_factor'] or 0]
  results.append({'exit_model':cid,'config':cfg,'admissible':ok,'by_entry':by,'combined':comb,
                  'robust':{'reward_risk':round(rr,3),'profit_factor':round(pf,3),'p10_return_pct':round(p10,3),'avg_loss_pct':round(avgloss,3),'max_loss_pct':round(maxloss,3),'mean_return_pct':round(mean,3),'win_rate_pct':round(wr,2)},'rank_key':rank})
 results.sort(key=lambda z:tuple(z['rank_key']),reverse=True)
 best=results[0] if results else None
 v2_anchor=next((z for z in results if z['config']['loss_cut_pct']==5.5 and z['config']['early_weeks']==2 and z['config']['early_floor_pct']==-1.0 and z['config']['decay_ratio'] is None and z['config']['trail_pct'] is None and z['config']['rsi_heat']==75.0),None)
 diag={'best_overall':best,
       'best_signal_decay':best_family(results,lambda c:c['decay_ratio'] is not None),
       'best_5pct_trailing':best_family(results,lambda c:c['trail_pct']==5.0),
       'v2_best_anchor_recomputed':v2_anchor}
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V3_SIGNAL_DECAY','generated_at_utc':datetime.now(timezone.utc).isoformat(),
          'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},
          'entries_fixed':ENTRIES,'exit_models_tested':len(CONFIGS),'top_exit_models':results[:30],'diagnostics':diag,
          'selection_rule':'Entries fixed. Require >=50 ALL and >=30 12M trades per entry, positive 12M mean, PF>=1.05 per entry, combined 12M PF>=1.10, endpoint share<=15%. Rank worst reward/risk across both entries and ALL/12M/18M/24M, then PF, P10, average loss, max loss, mean and win rate.',
          'signal_decay_definition':'Fixed-entry continuous-strength ratio is evaluated on the completed week only; exit requires threshold breach plus weekly close reversal; no entry parameter is changed.',
          'requested_5pct_trailing_explicitly_tested':True,
          'trailing_definition':'5% family arms only after a completed-week peak gain reaches activation; completed-week peak-to-close drawdown plus close reversal signals exit at next-week open.',
          'endpoint_accounting':'Open endpoint positions marked at final completed-week close for evaluation only.',
          'lookahead_controls':{'signals':'completed_week_only','execution':'next_week_open','intrabar_stop_assumption':False,'endpoint_mark_is_execution':False},
          'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','WEEKLY_CLOSE_CONFIRMED_STOPS_NOT_INTRABAR','ENDPOINT_MARK_TO_MARKET_FOR_CENSORING_CONTROL','RESEARCH_ONLY']}
 OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
