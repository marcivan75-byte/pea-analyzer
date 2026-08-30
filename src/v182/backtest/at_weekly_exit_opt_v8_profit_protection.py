"""Research-only V8: profit-protection/reversal bench with fixed entries and locked 9% protective stop.
All strategic signals use completed-week information and execute next weekly open.
The 5% trailing rule is close-confirmed drawdown after reversal, not an intrabar stop.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, math, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files,_iter_consolidated,CACHE_DIRS,MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import entry_mask
from .at_weekly_exit_bench_v1 import ENTRIES, summarize
from .at_weekly_exit_opt_v7_protective_stop import fixed_stop_fill

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V8_PROFIT_PROTECTION.json'
STOP_PCT=9.0

# Frozen early-invalidations inherited from validated V6 family.
EARLY={'loss_cut_pct':4.5,'fp_weeks':2,'fp_floor_pct':-1.5,'rsi_heat':75.0,'stoch_heat':75.0}

MODELS=[
 {'id':'V8_BASE','activation':None,'confirm':99,'trail5':False},
 {'id':'V8_TRAIL5_REV_A5','activation':5.0,'confirm':99,'trail5':True},
 {'id':'V8_TRAIL5_REV_A8','activation':8.0,'confirm':99,'trail5':True},
 {'id':'V8_TRAIL5_REV_A12','activation':12.0,'confirm':99,'trail5':True},
 {'id':'V8_CONFL_A5_C1','activation':5.0,'confirm':1,'trail5':False},
 {'id':'V8_CONFL_A5_C2','activation':5.0,'confirm':2,'trail5':False},
 {'id':'V8_CONFL_A8_C1','activation':8.0,'confirm':1,'trail5':False},
 {'id':'V8_CONFL_A8_C2','activation':8.0,'confirm':2,'trail5':False},
 {'id':'V8_CONFL_A8_C3','activation':8.0,'confirm':3,'trail5':False},
 {'id':'V8_CONFL_A12_C1','activation':12.0,'confirm':1,'trail5':False},
 {'id':'V8_CONFL_A12_C2','activation':12.0,'confirm':2,'trail5':False},
 {'id':'V8_CONFL_A12_C3','activation':12.0,'confirm':3,'trail5':False},
 {'id':'V8_COMBO_A8_C2_TRAIL5','activation':8.0,'confirm':2,'trail5':True},
 {'id':'V8_COMBO_A12_C2_TRAIL5','activation':12.0,'confirm':2,'trail5':True},
]

def adx(frame,period=14):
 h,l,c=frame.high,frame.low,frame.close
 up=h.diff(); dn=-l.diff()
 plus=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=frame.index)
 minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=frame.index)
 tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
 atr=tr.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
 pdi=100*plus.ewm(alpha=1/period,adjust=False,min_periods=period).mean()/atr.replace(0,np.nan)
 mdi=100*minus.ewm(alpha=1/period,adjust=False,min_periods=period).mean()/atr.replace(0,np.nan)
 dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
 return dx.ewm(alpha=1/period,adjust=False,min_periods=period).mean(),pdi,mdi

def daily_drop_by_week(hist):
 d=hist.copy(); d.index=pd.to_datetime(d.index); r=d.close.pct_change()*100
 # minimum daily close-to-close move observed inside each completed week
 return r.resample('W-FRI',label='right',closed='right').min()

def augment(b,hist):
 b=b.copy(); mid=b.close.rolling(20,min_periods=20).mean(); sd=b.close.rolling(20,min_periods=20).std(ddof=0)
 b['bb_upper']=mid+2*sd
 b['adx'],b['plus_di'],b['minus_di']=adx(b)
 b['daily_drop_min_pct']=daily_drop_by_week(hist).reindex(b.index)
 return b

def arrs(b):
 return {k:b[k].to_numpy(float) for k in ['open','high','low','close','rsi14','stoch_k','stoch_d','psar','sma20','sma50','bb_upper','adx','plus_di','minus_di','daily_drop_min_pct']}

def confirmed_fp(i,a):
 c=a['close']; r=a['rsi14']; k=a['stoch_k']; d=a['stoch_d']; ps=a['psar']; s20=a['sma20']
 return bool((np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i]<d[i] and k[i-1]>=d[i-1]) or
             (np.isfinite(r[i]) and np.isfinite(r[i-1]) and r[i]<r[i-1]) or
             (np.isfinite(ps[i]) and c[i]<ps[i]) or (np.isfinite(s20[i]) and c[i]<s20[i]))

def profit_signals(i,a):
 c=a['close']; r=a['rsi14']; k=a['stoch_k']; d=a['stoch_d']; ps=a['psar']; s20=a['sma20']; s50=a['sma50']; bb=a['bb_upper']; ax=a['adx']; p=a['plus_di']; m=a['minus_di']; dd=a['daily_drop_min_pct']
 sig={}
 sig['rsi_overheat_reversal']=bool(np.isfinite(r[i]) and r[i]>75 and r[i]<r[i-1] and c[i]<c[i-1])
 sig['stoch_overheat_crossdown']=bool(np.isfinite(k[i]) and k[i]>75 and np.isfinite(d[i]) and k[i]<d[i] and k[i-1]>=d[i-1])
 sig['psar_break']=bool(np.isfinite(ps[i]) and c[i]<ps[i])
 sig['sma20_break']=bool(np.isfinite(s20[i]) and c[i]<s20[i])
 sig['sma50_break']=bool(np.isfinite(s50[i]) and c[i]<s50[i])
 sig['bb_upper_reentry']=bool(np.isfinite(bb[i]) and np.isfinite(bb[i-1]) and c[i-1]>=bb[i-1] and c[i]<bb[i])
 sig['adx_degrading']=bool(i>=2 and np.isfinite(ax[i]) and np.isfinite(ax[i-1]) and np.isfinite(ax[i-2]) and ax[i-2]>=25 and ax[i]<ax[i-1]<ax[i-2] and ((np.isfinite(p[i]) and np.isfinite(p[i-1]) and p[i]<p[i-1]) or (np.isfinite(m[i]) and np.isfinite(m[i-1]) and m[i]>m[i-1])))
 sig['daily_drop_5']=bool(np.isfinite(dd[i]) and dd[i]<=-5.0)
 return sig

def trade(sym,entry_label,model,b,ei,ep,xi,xp,reasons,endpoint=False,signal_close=None,stop_price=None):
 return {'symbol':sym,'entry_model':entry_label,'exit_model':model,'entry_date':b.index[ei].date().isoformat(),'exit_date':b.index[xi].date().isoformat(),
  'entry_price':round(float(ep),6),'exit_price':round(float(xp),6),'return_pct':(float(xp)/float(ep)-1)*100,'holding_weeks':max(0,xi-ei),
  'exit_reasons':'|'.join(reasons),'endpoint_mark':bool(endpoint),'signal_close':None if signal_close is None else round(float(signal_close),6),'stop_price':None if stop_price is None else round(float(stop_price),6)}

def simulate(sym,b,a,sig,entry_label,model):
 o,l,c,rsi,k,d,psar=(a[x] for x in ['open','low','close','rsi14','stoch_k','stoch_d','psar'])
 pos=-1; price=0.; peak_close=0.; out=[]
 for i in range(1,len(c)):
  if pos>=0 and i>=pos:
   sp=price*(1-STOP_PCT/100); fill=fixed_stop_fill(o[i],l[i],sp)
   if fill is not None:
    fp,kind=fill; out.append(trade(sym,entry_label,model['id'],b,pos,price,i,fp,[f'PROTECTIVE_STOP_{STOP_PCT:g}_{kind}'],stop_price=sp)); pos=-1; price=0.; peak_close=0.; continue
   if np.isfinite(c[i]): peak_close=max(peak_close,float(c[i]))
   if i<len(c)-1:
    prev=c[i-1]; ret=(c[i]/price-1)*100; held=i-pos+1; reasons=[]
    if ret<=-EARLY['loss_cut_pct']: reasons.append('EARLY_LOSS_CLOSE')
    if held<=EARLY['fp_weeks'] and ret<=EARLY['fp_floor_pct'] and np.isfinite(prev) and c[i]<prev and confirmed_fp(i,a): reasons.append('CONFIRMED_FALSE_POSITIVE')
    # Baseline strategic trend safety retained.
    if np.isfinite(psar[i]) and c[i]<psar[i]: reasons.append('CLOSE_LT_PSAR')
    activation=model['activation']; activated=activation is not None and peak_close>=price*(1+activation/100)
    if activated and not reasons:
     sigs=profit_signals(i,a); active=[name for name,v in sigs.items() if v]
     if len(active)>=model['confirm']: reasons.append('PROFIT_CONFLUENCE_'+str(len(active))+'_'+'_'.join(active))
     if model['trail5'] and peak_close>0 and c[i]<=peak_close*.95 and c[i]<prev: reasons.append('TRAIL5_CLOSE_CONFIRMED_REVERSAL')
    if reasons and np.isfinite(o[i+1]) and o[i+1]>0:
     out.append(trade(sym,entry_label,model['id'],b,pos,price,i+1,float(o[i+1]),reasons,signal_close=c[i])); pos=-1; price=0.; peak_close=0.; continue
  if pos<0 and i<len(c)-1 and sig[i] and np.isfinite(o[i+1]) and o[i+1]>0:
   pos=i+1; price=float(o[i+1]); peak_close=price
 if pos>=0 and np.isfinite(c[-1]) and c[-1]>0:
  out.append(trade(sym,entry_label,model['id'],b,pos,price,len(c)-1,float(c[-1]),['ENDPOINT_MARK'],endpoint=True,signal_close=c[-1]))
 return out

def robust_stats(by):
 rr=[]; pf=[]; means=[]; p10=[]; n=[]
 for e in by.values():
  for w in ['ALL','12M','18M','24M']:
   z=e[w]
   if z['trades']>=15:
    n.append(z['trades'])
    if z.get('reward_risk') is not None: rr.append(float(z['reward_risk']))
    if z.get('profit_factor') is not None: pf.append(float(z['profit_factor']))
    if z.get('mean_return_pct') is not None: means.append(float(z['mean_return_pct']))
    if z.get('p10_return_pct') is not None: p10.append(float(z['p10_return_pct']))
 return {'min_rr':round(min(rr),3) if rr else None,'min_pf':round(min(pf),3) if pf else None,'min_mean':round(min(means),3) if means else None,'worst_p10':round(min(p10),3) if p10 else None,'min_window_trades':min(n) if n else 0}

def run():
 st=time.perf_counter(); bars={}; aa={}; sigs={}; first=[]; last=[]
 for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
  for sym,hist,err in _iter_consolidated(path):
   if sym is None or err or hist is None or hist.empty: continue
   w=_to_weekly(hist)
   if len(w)<MIN_WEEKLY_BARS: continue
   b=augment(build_bars(w),hist)
   bars[sym]=b; aa[sym]=arrs(b); first.append(b.index.min()); last.append(b.index.max())
   sigs[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
 end=max(last); results=[]
 for m in MODELS:
  by={}; combined=[]
  for e in ENTRIES:
   tr=[]
   for sym,b in bars.items(): tr.extend(simulate(sym,b,aa[sym],sigs[sym][e['label']],e['label'],m))
   by[e['label']]=summarize(pd.DataFrame(tr),end); combined.extend(tr)
  df=pd.DataFrame(combined); robust=robust_stats(by)
  results.append({'exit_model':m['id'],'config':m,'by_entry':by,'combined':summarize(df,end),'robust':robust,
    'tail_counts':{f'lt_{x}pct':int((df.return_pct<=-x).sum()) for x in [10,15,20]},'worst_10_trades':df.sort_values('return_pct').head(10).to_dict('records')})
 valid=[z for z in results if z['robust']['min_window_trades']>=15 and z['combined']['ALL']['trades']>=100]
 def key(z):
  r=z['robust']; return (r['min_rr'] or -99,r['min_pf'] or -99,r['min_mean'] or -99,r['worst_p10'] or -99,z['combined']['ALL']['mean_return_pct'] or -99)
 valid.sort(key=key,reverse=True)
 payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V8_PROFIT_PROTECTION','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),
  'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'entries_fixed':ENTRIES,'protective_stop_pct_locked':STOP_PCT,
  'models_tested':len(MODELS),'models':results,'top_robust':valid[:5],
  'signal_blocks':{'early_invalidation':'frozen V6 family','profit_protection':['RSI>75 reversal','stoch>75 crossdown','PSAR break','SMA20/50 break','BB upper re-entry','ADX degradation with DI deterioration','>=5% daily close-to-close drop observed within completed week','5% peak-close trailing drawdown + reversal']},
  'lookahead_controls':{'entry_models_fixed':True,'protective_stop_locked':True,'strategic_signals':'completed_week_only','strategic_execution':'next_week_open','daily_drop_used_only_after_week_completed':True,'trailing_uses_completed_week_closes_only':True,'endpoint_mark_is_execution':False},
  'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','DAILY_DROP_SIGNAL_EXECUTES_NEXT_WEEK_OPEN','WEEKLY_TRAILING_IS_CLOSE_CONFIRMED_NOT_INTRABAR','RESEARCH_ONLY']}
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
