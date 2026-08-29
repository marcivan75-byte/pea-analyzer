"""Research-only weekly exit test bench for the two retained optimized entry models.
Completed-week signals only; all discretionary exits execute at next-week open.
No production/order impact.
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

# Broad, pre-specified exit families. Stops use completed-week close confirmation then next-week-open execution.
CONFIGS=[]
for hard in [None,5,7,9]:
 for trail in [None,5,7]:
  for heat in ['none','rsi','stoch','both']:
   for trend in ['none','psar','sma20','psar_sma20']:
    if hard is None and trail is None and heat=='none' and trend=='none':
     continue
    # keep first pass broad but bounded
    if len(CONFIGS)>=72: break
    CONFIGS.append({'hard_stop_pct':hard,'trail_pct':trail,'heat':heat,'trend':trend,'profit_lock_pct':None})
   if len(CONFIGS)>=72: break
  if len(CONFIGS)>=72: break
 if len(CONFIGS)>=72: break
# Explicit profit-lock / reversal candidates.
CONFIGS += [
 {'hard_stop_pct':5,'trail_pct':5,'heat':'both','trend':'psar','profit_lock_pct':p} for p in [5,8,10,12]
] + [
 {'hard_stop_pct':7,'trail_pct':5,'heat':'both','trend':'psar_sma20','profit_lock_pct':p} for p in [8,10,12]
]


def _cross_down(a,b,i):
 if i<=0: return False
 vals=[a.iloc[i],b.iloc[i],a.iloc[i-1],b.iloc[i-1]]
 return all(np.isfinite(float(x)) for x in vals) and float(a.iloc[i])<float(b.iloc[i]) and float(a.iloc[i-1])>=float(b.iloc[i-1])


def exit_reasons(b,i,pos,cfg):
 row=b.iloc[i]; close=float(row.close); prev=float(b.close.iloc[i-1]) if i>0 else close
 reasons=[]; ret=(close/pos['price']-1)*100
 hard=cfg['hard_stop_pct']
 if hard is not None and ret<=-float(hard): reasons.append(f'HARD_CLOSE_{hard}')
 trail=cfg['trail_pct']; peak=max(pos['peak_close'],close); dd=(close/peak-1)*100
 if trail is not None and peak>pos['price'] and dd<=-float(trail) and close<prev: reasons.append(f'TRAIL_{trail}_REV')
 heat=cfg['heat']; rsi=float(row.rsi14) if np.isfinite(float(row.rsi14)) else np.nan; k=float(row.stoch_k) if np.isfinite(float(row.stoch_k)) else np.nan
 if heat in {'rsi','both'} and np.isfinite(rsi) and rsi>75 and close<prev: reasons.append('RSI_GT75_REV')
 if heat in {'stoch','both'} and np.isfinite(k) and k>75 and _cross_down(b.stoch_k,b.stoch_d,i): reasons.append('STOCH_GT75_CROSSDOWN')
 trend=cfg['trend']
 if trend in {'psar','psar_sma20'} and np.isfinite(float(row.psar)) and close<float(row.psar): reasons.append('CLOSE_LT_PSAR')
 if trend in {'sma20','psar_sma20'} and np.isfinite(float(row.sma20)) and close<float(row.sma20): reasons.append('CLOSE_LT_SMA20')
 lock=cfg['profit_lock_pct']
 if lock is not None and ret>=float(lock) and close<prev and ((np.isfinite(rsi) and rsi>70) or (np.isfinite(k) and k>75)):
  reasons.append(f'PROFIT_LOCK_{lock}_REV')
 return reasons,peak


def simulate(sym,b,entry,cfg,cfg_id):
 sig=entry_mask(b,entry['weights'],entry['threshold_ratio']); pos=None; out=[]
 for i in range(1,len(b)-1):
  nxt=b.iloc[i+1]
  if pos is not None and i>=pos['idx']:
   reasons,peak=exit_reasons(b,i,pos,cfg); pos['peak_close']=peak
   if reasons and np.isfinite(float(nxt.open)) and float(nxt.open)>0:
    xp=float(nxt.open); out.append({'symbol':sym,'entry_model':entry['label'],'exit_model':cfg_id,'entry_date':b.index[pos['idx']].date().isoformat(),'exit_date':b.index[i+1].date().isoformat(),'return_pct':(xp/pos['price']-1)*100,'holding_weeks':i+1-pos['idx'],'exit_reasons':'|'.join(reasons)}); pos=None; continue
  if pos is None and bool(sig.iloc[i]) and np.isfinite(float(nxt.open)) and float(nxt.open)>0:
   pos={'idx':i+1,'price':float(nxt.open),'peak_close':float(nxt.open)}
 return out


def metric(df):
 if df.empty: return {'trades':0,'win_rate_pct':None,'mean_return_pct':None,'profit_factor':None,'reward_risk':None,'p10_return_pct':None,'avg_loss_pct':None,'avg_win_pct':None,'max_loss_pct':None}
 r=df.return_pct.astype(float).to_numpy(); win=r[r>0]; loss=r[r<0]
 aw=float(win.mean()) if len(win) else 0.0; al=float(loss.mean()) if len(loss) else 0.0
 pf=float(win.sum()/(-loss.sum())) if len(loss) and -loss.sum()>0 else 99.0
 rr=float(aw/abs(al)) if al<0 else 99.0
 return {'trades':len(r),'win_rate_pct':round(float((r>0).mean()*100),2),'mean_return_pct':round(float(r.mean()),3),'profit_factor':round(pf,3),'reward_risk':round(rr,3),'p10_return_pct':round(float(np.quantile(r,.10)),3),'avg_loss_pct':round(al,3),'avg_win_pct':round(aw,3),'max_loss_pct':round(float(r.min()),3)}


def summarize(df,end):
 res={'ALL':metric(df)}
 d=pd.to_datetime(df.entry_date,errors='coerce') if not df.empty else pd.Series(dtype='datetime64[ns]')
 for lab,m in [('12M',12),('18M',18),('24M',24),('36M',36)]: res[lab]=metric(df[d>=end-pd.DateOffset(months=m)]) if not df.empty else metric(df)
 vals=[res[k] for k in ['ALL','12M','18M','24M'] if res[k]['trades']>=15]
 robust_rr=min((x['reward_risk'] or 0) for x in vals) if vals else 0
 robust_pf=min((x['profit_factor'] or 0) for x in vals) if vals else 0
 robust_mean=min((x['mean_return_pct'] if x['mean_return_pct'] is not None else -999) for x in vals) if vals else -999
 worst_p10=min((x['p10_return_pct'] if x['p10_return_pct'] is not None else -999) for x in vals) if vals else -999
 a=res['ALL']; res['selection_key']=[round(robust_rr,4),round(robust_pf,4),round(worst_p10,4),round(robust_mean,4),a['win_rate_pct'] or 0,min(a['trades'],250)]
 return res


def run():
 st=time.perf_counter(); OUT.parent.mkdir(parents=True,exist_ok=True); bars={}; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=build_bars(w); bars[sym]=b; first.append(b.index.min()); last.append(b.index.max())
 end=max(last); results=[]
 for ci,cfg in enumerate(CONFIGS,1):
  cfg_id=f'EXIT_{ci:03d}'; all_tr=[]; by_entry={}
  for ent in ENTRIES:
   tr=[]
   for sym,b in bars.items(): tr.extend(simulate(sym,b,ent,cfg,cfg_id))
   df=pd.DataFrame(tr); by_entry[ent['label']]=summarize(df,end); all_tr.extend(tr)
  all_df=pd.DataFrame(all_tr); combined=summarize(all_df,end)
  # safeguard: require >=50 trades per entry model; rank on weakest entry-family robustness first.
  eligible=all(by_entry[e['label']]['ALL']['trades']>=50 for e in ENTRIES)
  weak_rr=min(by_entry[e['label']]['selection_key'][0] for e in ENTRIES)
  weak_pf=min(by_entry[e['label']]['selection_key'][1] for e in ENTRIES)
  key=[1 if eligible else 0,weak_rr,weak_pf,*combined['selection_key']]
  results.append({'exit_model':cfg_id,'config':cfg,'eligible_sample':eligible,'by_entry':by_entry,'combined':combined,'rank_key':key})
 results.sort(key=lambda z:tuple(z['rank_key']),reverse=True)
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_BENCH_V1','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'entries_fixed':ENTRIES,'exit_models_tested':len(CONFIGS),'top_exit_models':results[:12],'best_exit_model':results[0] if results else None,'selection_rule':'Sample safeguard >=50 trades for each retained entry family; maximize weakest robust reward/risk then weakest robust PF, combined P10/mean/win rate.','trailing_definition':'Completed-week close at least trail_pct below peak completed-week close, with close below prior completed-week close; execute next-week open.','lookahead_controls':{'signals':'completed_week_only','execution':'next_week_open','intrabar_stop_assumption':False},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','WEEKLY_CLOSE_CONFIRMED_STOPS_NOT_INTRABAR','RESEARCH_ONLY']}
 OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload
if __name__=='__main__': run()
