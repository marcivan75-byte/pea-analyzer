"""Research-only V8: four-block exit architecture.

LOCKED invariants:
1) fixed protective stop = 9%, standing from entry;
2) entry models unchanged;
3) early false-positive block unchanged from validated V6 family;
4) profit protection activates only after an unrealised gain threshold;
5) take-profit exits require a completed-week reversal/confluence signal and execute next-week open.

New reversal evidence tested: last daily close change vs J-1, Bollinger upper-band re-entry,
ADX/+DI deterioration, RSI reversal, stochastic cross-down, PSAR/SMA20/SMA50 breaks.
No production influence.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import itertools, json, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files,_iter_consolidated,CACHE_DIRS,MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import entry_mask
from .at_weekly_exit_bench_v1 import ENTRIES, summarize
from .at_weekly_exit_opt_v7_protective_stop import fixed_stop_fill

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_EXIT_OPT_V8_PROFIT_REVERSAL_BLOCKS.json'
STOP_PCT=9.0
FP_WEEKS=2
FP_FLOOR=-1.5
ACTIVATIONS=[5.0,8.0,12.0,15.0,20.0]
SCORES=[1,2,3]
DAILY_DROP_THRESHOLDS=[3.0,4.0,5.0]
FAMILIES={
 'MOMENTUM':['daily_drop','rsi_rev','stoch_rev'],
 'BB_MOMENTUM':['daily_drop','bb_reentry','rsi_rev','stoch_rev'],
 'TREND':['adx_decay','psar_break','sma20_break','sma50_break'],
 'FULL':['daily_drop','bb_reentry','adx_decay','rsi_rev','stoch_rev','psar_break','sma20_break','sma50_break'],
}


def add_indicators(b:pd.DataFrame)->pd.DataFrame:
    x=b.copy()
    m=x.close.rolling(20,min_periods=20).mean(); sd=x.close.rolling(20,min_periods=20).std(ddof=0)
    x['bb_upper']=m+2*sd
    up=x.high.diff(); dn=-x.low.diff()
    plus_dm=up.where((up>dn)&(up>0),0.0); minus_dm=dn.where((dn>up)&(dn>0),0.0)
    tr=pd.concat([(x.high-x.low),(x.high-x.close.shift()).abs(),(x.low-x.close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    pdi=100*plus_dm.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/atr.replace(0,np.nan)
    mdi=100*minus_dm.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/atr.replace(0,np.nan)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    x['plus_di']=pdi; x['minus_di']=mdi; x['adx']=dx.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    return x


def last_daily_change_by_week(hist:pd.DataFrame,index:pd.DatetimeIndex)->np.ndarray:
    d=hist.copy().sort_index(); ch=d.close.pct_change()*100
    wk=ch.resample('W-FRI',label='right',closed='right').last()
    return wk.reindex(index).to_numpy(float)


def evidence(i,b,a,daily_drop_threshold):
    c=a['close']; r=a['rsi']; k=a['k']; d=a['d']; psar=a['psar']; s20=a['sma20']; s50=a['sma50']; bb=a['bb']; adx=a['adx']; pdi=a['pdi']; m=a['daily']
    prev=c[i-1]
    return {
      'daily_drop':bool(np.isfinite(m[i]) and m[i] <= -daily_drop_threshold),
      'bb_reentry':bool(np.isfinite(bb[i]) and np.isfinite(bb[i-1]) and c[i-1]>=bb[i-1] and c[i]<bb[i]),
      'adx_decay':bool(np.isfinite(adx[i]) and np.isfinite(adx[i-1]) and np.isfinite(pdi[i]) and np.isfinite(pdi[i-1]) and adx[i-1]>=25 and adx[i]<adx[i-1] and pdi[i]<pdi[i-1]),
      'rsi_rev':bool(np.isfinite(r[i]) and np.isfinite(r[i-1]) and r[i-1]>=70 and r[i]<r[i-1] and c[i]<prev),
      'stoch_rev':bool(np.isfinite(k[i]) and np.isfinite(d[i]) and np.isfinite(k[i-1]) and np.isfinite(d[i-1]) and k[i-1]>=75 and k[i]<d[i] and k[i-1]>=d[i-1]),
      'psar_break':bool(np.isfinite(psar[i]) and c[i]<psar[i]),
      'sma20_break':bool(np.isfinite(s20[i]) and c[i]<s20[i] and c[i-1]>=s20[i-1]),
      'sma50_break':bool(np.isfinite(s50[i]) and c[i]<s50[i] and c[i-1]>=s50[i-1]),
    }


def fp_confirm(i,a):
    e=evidence(i,None,a,5.0)
    return e['stoch_rev'] or (np.isfinite(a['rsi'][i]) and a['rsi'][i]<a['rsi'][i-1]) or e['psar_break'] or e['sma20_break']


def trade(sym,entry_label,model,b,ei,ep,xi,xp,reasons,endpoint=False,signal_close=None,stop_price=None,peak_ret=None):
    return {'symbol':sym,'entry_model':entry_label,'exit_model':model,'entry_date':b.index[ei].date().isoformat(),
      'exit_date':b.index[xi].date().isoformat(),'entry_price':round(float(ep),6),'exit_price':round(float(xp),6),
      'return_pct':(float(xp)/float(ep)-1)*100,'holding_weeks':max(0,xi-ei),'exit_reasons':'|'.join(reasons),
      'endpoint_mark':bool(endpoint),'signal_close':None if signal_close is None else round(float(signal_close),6),
      'stop_price':None if stop_price is None else round(float(stop_price),6),'peak_return_pct':None if peak_ret is None else round(float(peak_ret),3)}


def simulate(sym,b,a,sig,entry_label,cfg,model):
    o,l,c=a['open'],a['low'],a['close']; pos=-1; price=0.; peak=0.; out=[]
    for i in range(1,len(c)):
      if pos>=0 and i>=pos:
        sp=price*(1-STOP_PCT/100); fill=fixed_stop_fill(o[i],l[i],sp)
        if fill is not None:
          xp,kind=fill; out.append(trade(sym,entry_label,model,b,pos,price,i,xp,[f'PROTECTIVE_STOP_{STOP_PCT:g}_{kind}'],stop_price=sp,peak_ret=peak)); pos=-1; price=0.; peak=0.; continue
        if np.isfinite(c[i]): peak=max(peak,(c[i]/price-1)*100)
        if i < len(c)-1:
          ret=(c[i]/price-1)*100; held=i-pos+1; reasons=[]
          # Block B: early false-positive invalidation, unchanged principle.
          if held<=FP_WEEKS and ret<=FP_FLOOR and c[i]<c[i-1] and fp_confirm(i,a): reasons.append('EARLY_FALSE_POSITIVE')
          # Blocks C+D: winner activation then reversal confluence.
          if peak>=cfg['activation_pct']:
            ev=evidence(i,b,a,cfg['daily_drop_pct']); hits=[k for k in cfg['family'] if ev[k]]
            if len(hits)>=cfg['score']:
              reasons.append('PROFIT_REVERSAL_'+'+'.join(hits))
          if reasons and np.isfinite(o[i+1]) and o[i+1]>0:
            out.append(trade(sym,entry_label,model,b,pos,price,i+1,float(o[i+1]),reasons,signal_close=c[i],peak_ret=peak)); pos=-1; price=0.; peak=0.; continue
      if pos<0 and i<len(c)-1 and sig[i] and np.isfinite(o[i+1]) and o[i+1]>0:
        pos=i+1; price=float(o[i+1]); peak=0.
    if pos>=0 and np.isfinite(c[-1]) and c[-1]>0:
      out.append(trade(sym,entry_label,model,b,pos,price,len(c)-1,float(c[-1]),['ENDPOINT_MARK'],endpoint=True,signal_close=c[-1],peak_ret=peak))
    return out


def robust_score(sm):
    eligible=[]
    for w in ['ALL','12M','18M','24M','36M']:
      z=sm[w]
      if z.get('trades',0)>=30 and z.get('profit_factor') is not None and z.get('mean_return_pct') is not None:
        eligible.append(z)
    if not eligible:return None
    min_pf=min(z['profit_factor'] for z in eligible); min_mean=min(z['mean_return_pct'] for z in eligible); min_rr=min(z['reward_risk'] for z in eligible if z.get('reward_risk') is not None); min_p10=min(z['p10_return_pct'] for z in eligible)
    return {'min_pf':round(min_pf,3),'min_mean':round(min_mean,3),'min_rr':round(min_rr,3),'min_p10':round(min_p10,3),'windows':len(eligible)}


def run():
    st=time.perf_counter(); bars={}; arr={}; sigs={}; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
      for sym,hist,err in _iter_consolidated(path):
        if sym is None or err or hist is None or hist.empty: continue
        w=_to_weekly(hist)
        if len(w)<MIN_WEEKLY_BARS: continue
        b=add_indicators(build_bars(w)); daily=last_daily_change_by_week(hist,b.index)
        need=['open','low','close','rsi14','stoch_k','stoch_d','psar','sma20','sma50','bb_upper','adx','plus_di']
        if any(x not in b.columns for x in need): continue
        bars[sym]=b; first.append(b.index.min()); last.append(b.index.max())
        arr[sym]={'open':b.open.to_numpy(float),'low':b.low.to_numpy(float),'close':b.close.to_numpy(float),'rsi':b.rsi14.to_numpy(float),'k':b.stoch_k.to_numpy(float),'d':b.stoch_d.to_numpy(float),'psar':b.psar.to_numpy(float),'sma20':b.sma20.to_numpy(float),'sma50':b.sma50.to_numpy(float),'bb':b.bb_upper.to_numpy(float),'adx':b.adx.to_numpy(float),'pdi':b.plus_di.to_numpy(float),'daily':daily}
        sigs[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
    end=max(last); models=[]
    configs=[]
    for fam_name,fam in FAMILIES.items():
      for act,score,dd in itertools.product(ACTIVATIONS,SCORES,DAILY_DROP_THRESHOLDS):
        if score>len(fam): continue
        configs.append({'family_name':fam_name,'family':fam,'activation_pct':act,'score':score,'daily_drop_pct':dd})
    for n,cfg in enumerate(configs,1):
      mid=f'V8_{n:04d}'; combined=[]; by={}; trig={}
      for e in ENTRIES:
        tr=[]
        for sym,b in bars.items(): tr.extend(simulate(sym,b,arr[sym],sigs[sym][e['label']],e['label'],cfg,mid))
        df=pd.DataFrame(tr); by[e['label']]=summarize(df,end); combined.extend(tr)
      df=pd.DataFrame(combined); sm=summarize(df,end); rb=robust_score(sm)
      if not df.empty:
        trig={'stop':int(df.exit_reasons.str.contains('PROTECTIVE_STOP').sum()),'false_positive':int(df.exit_reasons.str.contains('EARLY_FALSE_POSITIVE').sum()),'profit_reversal':int(df.exit_reasons.str.contains('PROFIT_REVERSAL').sum()),'endpoint':int(df.endpoint_mark.sum())}
      models.append({'exit_model':mid,'config':cfg,'by_entry':by,'combined':sm,'robust':rb,'trigger_counts':trig,
        'tail_counts':{f'lt_{x}pct':int((df.return_pct<=-x).sum()) for x in [10,15,20]} if not df.empty else {},
        'worst_10':df.sort_values('return_pct').head(10).to_dict('records') if not df.empty else []})
    valid=[z for z in models if z['robust'] is not None and z['combined']['ALL']['trades']>=100]
    # Risk first, then preservation of profitability and reward/risk.
    valid.sort(key=lambda z:(z['combined']['ALL']['max_loss_pct'],z['robust']['min_p10'],z['robust']['min_pf'],z['robust']['min_rr'],z['robust']['min_mean']),reverse=True)
    best=valid[0] if valid else None
    payload={'status':'SUCCESS','version':'AT_WEEKLY_EXIT_OPT_V8_PROFIT_REVERSAL_BLOCKS','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),
      'valid_actions':len(bars),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'entries_fixed':ENTRIES,'protective_stop_pct_locked':STOP_PCT,
      'blocks':{'A':'standing protective stop 9%','B':'early false-positive invalidation','C':'winner activation after peak unrealised gain','D':'take-profit on reversal confluence'},
      'criteria_definitions':{'daily_drop':'last trading-day close change versus J-1 within completed week','bb_reentry':'previous close at/above upper BB then current close below upper BB','adx_decay':'prior ADX>=25, ADX falling and +DI falling','rsi_rev':'prior RSI>=70 then RSI and price fall','stoch_rev':'prior K>=75 then bearish K/D cross','psar_break':'close below PSAR','sma20_break':'fresh close below SMA20','sma50_break':'fresh close below SMA50'},
      'models_tested':len(models),'best_risk_first':best,'top20':valid[:20],
      'lookahead_controls':{'entry_models_fixed':True,'protective_stop_locked_9':True,'fixed_stop_known_before_bar':True,'strategic_signals':'completed_week_only','strategic_execution':'next_week_open','daily_j1_used_only_as_completed_week_evidence':True,'endpoint_mark_is_execution':False},
      'limitations':['DAILY_J1_SIGNAL_IS_EVIDENCE_AT_COMPLETED_WEEK_CLOSE_NOT_INTRAWEEK_EXECUTION','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','RESEARCH_ONLY']}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
