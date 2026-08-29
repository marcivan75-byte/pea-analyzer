"""Weighted weekly ACTION entry simulations focused on false-positive reduction.
Research only. Existing AT V1.1 core entry remains mandatory; current confirmations plus breakout are scored.
Exit rules remain exactly AT V1.1; signal at weekly close, execution at next weekly open.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json, math, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _indicators, _exit_reasons, _to_weekly
from .at_weekly_v1_fixed import _cache_files, _iter_consolidated, CACHE_DIRS, MIN_WEEKLY_BARS

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHTED_ENTRY_V1.json'
OUT_CSV=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHTED_ENTRY_V1_TRADES.csv'

# Pre-declared, not chosen after observing this run.
# Breakout contribution is hierarchical (max one breakout score) to avoid double-counting nested conditions.
SCHEMES={
 'SELECTIVE_A': {'ma20_gt_ma50':5,'close_loc':5,'r1':15,'macd':15,
                 'near10':10,'near20':15,'break10':30,'break20':50},
 'SELECTIVE_B': {'ma20_gt_ma50':5,'close_loc':5,'r1':20,'macd':20,
                 'near10':10,'near20':20,'break10':40,'break20':60},
}
VARIANTS=[
 'REFERENCE_CURRENT','REFERENCE_PLUS_BREAK10','REFERENCE_PLUS_BREAK20',
 'A_GE_40','A_GE_55','A_GE_70','A_GE_85',
 'B_GE_50','B_GE_70','B_GE_90','B_GE_105'
]


def metrics(df):
    if df.empty:
        return {'trades':0,'win_rate_pct':None,'false_positive_pct':None,'mean_return_pct':None,'median_return_pct':None,'profit_factor':None,'p10_return_pct':None,'one_week_trades':0,'one_week_win_rate_pct':None,'one_week_mean_return_pct':None,'mean_holding_weeks':None}
    r=pd.to_numeric(df.return_pct,errors='coerce').dropna(); w=r[r>0]; l=r[r<0]
    pf=float(w.sum()/(-l.sum())) if len(l) and -l.sum()>0 else None
    one=df[pd.to_numeric(df.holding_weeks,errors='coerce')<=1]; rr=pd.to_numeric(one.return_pct,errors='coerce').dropna()
    wr=float((r>0).mean()*100)
    return {'trades':int(len(r)),'win_rate_pct':round(wr,2),'false_positive_pct':round(100-wr,2),
            'mean_return_pct':round(float(r.mean()),3),'median_return_pct':round(float(r.median()),3),
            'profit_factor':round(pf,3) if pf is not None and math.isfinite(pf) else None,
            'p10_return_pct':round(float(r.quantile(.10)),3),'one_week_trades':int(len(rr)),
            'one_week_win_rate_pct':round(float((rr>0).mean()*100),2) if len(rr) else None,
            'one_week_mean_return_pct':round(float(rr.mean()),3) if len(rr) else None,
            'mean_holding_weeks':round(float(pd.to_numeric(df.holding_weeks,errors='coerce').mean()),2)}


def build_bars(weekly):
    b=_indicators(weekly)
    ph=b['high'].shift(1); pl=b['low'].shift(1); pc=b['close'].shift(1)
    pivot=(ph+pl+pc)/3.0; b['r1_prev']=2.0*pivot-pl
    ema12=b['close'].ewm(span=12,adjust=False,min_periods=12).mean(); ema26=b['close'].ewm(span=26,adjust=False,min_periods=26).mean()
    b['macd']=ema12-ema26; b['macd_signal']=b['macd'].ewm(span=9,adjust=False,min_periods=9).mean()
    rng=(b['high']-b['low']).replace(0,np.nan); b['close_location']=(b['close']-b['low'])/rng
    b['prior_high_10w']=b['high'].shift(1).rolling(10,min_periods=10).max()
    b['prior_high_20w']=b['high'].shift(1).rolling(20,min_periods=20).max()
    return b


def flags(b):
    return {
      'ma20_gt_ma50':(b['sma20']>b['sma50']).fillna(False),
      'close_loc':(b['close_location']>=.60).fillna(False),
      'r1':(b['close']>b['r1_prev']).fillna(False),
      'macd':(b['macd']>b['macd_signal']).fillna(False),
      'near10':(b['close']>=.97*b['prior_high_10w']).fillna(False),
      'near20':(b['close']>=.97*b['prior_high_20w']).fillna(False),
      'break10':(b['close']>b['prior_high_10w']).fillna(False),
      'break20':(b['close']>b['prior_high_20w']).fillna(False),
    }


def score_series(f, scheme):
    w=SCHEMES[scheme]
    s=(f['ma20_gt_ma50'].astype(int)*w['ma20_gt_ma50']+
       f['close_loc'].astype(int)*w['close_loc']+
       f['r1'].astype(int)*w['r1']+
       f['macd'].astype(int)*w['macd'])
    # Hierarchical breakout strength: only the strongest state contributes.
    breakout=pd.Series(0,index=s.index,dtype=float)
    breakout=np.where(f['near10'],w['near10'],breakout)
    breakout=np.where(f['near20'],np.maximum(breakout,w['near20']),breakout)
    breakout=np.where(f['break10'],np.maximum(breakout,w['break10']),breakout)
    breakout=np.where(f['break20'],np.maximum(breakout,w['break20']),breakout)
    return s+pd.Series(breakout,index=s.index)


def entry_mask(b,v):
    core=b['entry_signal'].fillna(False)
    f=flags(b)
    current=core & f['ma20_gt_ma50'] & f['close_loc'] & f['r1'] & f['macd']
    if v=='REFERENCE_CURRENT': return current
    if v=='REFERENCE_PLUS_BREAK10': return current & f['break10']
    if v=='REFERENCE_PLUS_BREAK20': return current & f['break20']
    if v.startswith('A_GE_'):
        threshold=float(v.split('_')[-1]); return core & (score_series(f,'SELECTIVE_A')>=threshold)
    if v.startswith('B_GE_'):
        threshold=float(v.split('_')[-1]); return core & (score_series(f,'SELECTIVE_B')>=threshold)
    raise ValueError(v)


def backtest(symbol,weekly,v):
    b=build_bars(weekly); entry=entry_mask(b,v); pos=None; out=[]
    for i in range(len(b)-1):
        row=b.iloc[i]; nxt=b.iloc[i+1]
        if pos is not None and i>=pos['entry_idx']:
            reasons=_exit_reasons(row)
            if reasons and np.isfinite(float(nxt.open)):
                xp=float(nxt.open); ret=(xp/pos['entry_price']-1)*100
                out.append({'symbol':symbol,'variant':v,'entry_date':b.index[pos['entry_idx']].date().isoformat(),
                            'exit_date':b.index[i+1].date().isoformat(),'return_pct':ret,
                            'holding_weeks':i+1-pos['entry_idx'],'exit_reasons':'|'.join(reasons)})
                pos=None; continue
        if pos is None and bool(entry.iloc[i]) and np.isfinite(float(nxt.open)):
            pos={'entry_idx':i+1,'entry_price':float(nxt.open)}
    return out,int(entry.sum())


def run():
    st=time.perf_counter(); trades=[]; counts=Counter(); valid=0; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for symbol,hist,error in _iter_consolidated(path):
            if symbol is None or error or hist is None or hist.empty: continue
            w=_to_weekly(hist)
            if len(w)<MIN_WEEKLY_BARS: continue
            valid+=1; first.append(w.index.min()); last.append(w.index.max())
            for v in VARIANTS:
                t,n=backtest(symbol,w,v); trades.extend(t); counts[v]+=n
    df=pd.DataFrame(trades)
    if not df.empty: df=df.sort_values(['variant','entry_date','symbol'])
    end=max(last) if last else pd.Timestamp.utcnow().tz_localize(None); results={}
    for v in VARIANTS:
        sub=df[df.variant.eq(v)] if not df.empty else pd.DataFrame(columns=['return_pct','holding_weeks','entry_date'])
        vd={'ALL':metrics(sub)}; dates=pd.to_datetime(sub.entry_date,errors='coerce') if not sub.empty else pd.Series(dtype='datetime64[ns]')
        for lab,months in [('12M',12),('18M',18),('24M',24),('36M',36)]:
            cutoff=end-pd.DateOffset(months=months); vd[lab]=metrics(sub[dates>=cutoff]) if not sub.empty else metrics(sub)
        vd['selected_entry_signals']=int(counts[v]); results[v]=vd
    payload={'status':'SUCCESS','version':'AT_WEEKLY_WEIGHTED_ENTRY_V1','generated_at_utc':datetime.now(timezone.utc).isoformat(),
      'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':valid,
      'data_window':{'first_week':min(first).date().isoformat() if first else None,'last_week':max(last).date().isoformat() if last else None},
      'exit_rules_changed':False,'core_entry_remains_mandatory':True,'target_false_positive_pct':30.0,
      'score_design':'Current confirmation criteria retained; breakout strongly overweighted; hierarchical breakout score avoids nested double-counting.',
      'weights':SCHEMES,'variants':VARIANTS,'results':results,
      'lookahead_controls':{'pivot_r1':'previous_completed_week_only','breakout_highs':'prior_completed_weeks_only','macd':'signal_week_close_only','execution':'next_week_open'},
      'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','PREDECLARED_EXPLORATORY_SCORE_GRID','RESEARCH_ONLY']}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');
    df.to_csv(OUT_CSV,sep=';',index=False,encoding='utf-8-sig'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
