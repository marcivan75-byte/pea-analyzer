"""AT weekly ACTION entry filters: Pivot/R1 and MACD impulse confirmations.
Research only. Exit rules are exactly AT V1.1; signals execute at next weekly open.
Pivot/R1 use ONLY the previous completed week to avoid look-ahead.
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
OUT=ROOT/'outputs/backtest/AT_WEEKLY_IMPULSE_ENTRY_FILTER_V1.json'
OUT_CSV=ROOT/'outputs/backtest/AT_WEEKLY_IMPULSE_ENTRY_FILTER_V1_TRADES.csv'
VARIANTS=['BASELINE','CURRENT_BEST','PIVOT','R1','MACD','MACD_ACCEL','PIVOT_MACD','R1_MACD','BEST_R1_MACD','BEST_R1_MACD_ACCEL']


def metrics(df):
    if df.empty:
        return {'trades':0,'win_rate_pct':None,'mean_return_pct':None,'median_return_pct':None,'profit_factor':None,'p10_return_pct':None,'one_week_trades':0,'one_week_win_rate_pct':None,'one_week_mean_return_pct':None,'mean_holding_weeks':None}
    r=pd.to_numeric(df.return_pct,errors='coerce').dropna(); w=r[r>0]; l=r[r<0]
    pf=float(w.sum()/(-l.sum())) if len(l) and -l.sum()>0 else None
    one=df[pd.to_numeric(df.holding_weeks,errors='coerce')<=1]; rr=pd.to_numeric(one.return_pct,errors='coerce').dropna()
    return {'trades':int(len(r)),'win_rate_pct':round(float((r>0).mean()*100),2),'mean_return_pct':round(float(r.mean()),3),'median_return_pct':round(float(r.median()),3),'profit_factor':round(pf,3) if pf is not None and math.isfinite(pf) else None,'p10_return_pct':round(float(r.quantile(.10)),3),'one_week_trades':int(len(rr)),'one_week_win_rate_pct':round(float((rr>0).mean()*100),2) if len(rr) else None,'one_week_mean_return_pct':round(float(rr.mean()),3) if len(rr) else None,'mean_holding_weeks':round(float(pd.to_numeric(df.holding_weeks,errors='coerce').mean()),2)}


def build_bars(weekly):
    b=_indicators(weekly)
    # Previous completed week's classic pivot levels: no current-week H/L leakage.
    ph=b['high'].shift(1); pl=b['low'].shift(1); pc=b['close'].shift(1)
    b['pivot_prev']=(ph+pl+pc)/3.0
    b['r1_prev']=2.0*b['pivot_prev']-pl
    ema12=b['close'].ewm(span=12,adjust=False,min_periods=12).mean()
    ema26=b['close'].ewm(span=26,adjust=False,min_periods=26).mean()
    b['macd']=ema12-ema26
    b['macd_signal']=b['macd'].ewm(span=9,adjust=False,min_periods=9).mean()
    b['macd_hist']=b['macd']-b['macd_signal']
    rng=(b['high']-b['low']).replace(0,np.nan)
    b['close_location']=(b['close']-b['low'])/rng
    b['ma20_gt_ma50']=b['sma20']>b['sma50']
    return b


def entry_mask(b,v):
    base=b['entry_signal'].fillna(False)
    pivot=(b['close']>b['pivot_prev']).fillna(False)
    r1=(b['close']>b['r1_prev']).fillna(False)
    macd=(b['macd']>b['macd_signal']).fillna(False)
    accel=(macd & (b['macd_hist']>0) & (b['macd_hist']>b['macd_hist'].shift(1))).fillna(False)
    best=(b['ma20_gt_ma50'].fillna(False) & (b['close_location']>=.60).fillna(False))
    return {
      'BASELINE':base,
      'CURRENT_BEST':base & best,
      'PIVOT':base & pivot,
      'R1':base & r1,
      'MACD':base & macd,
      'MACD_ACCEL':base & accel,
      'PIVOT_MACD':base & pivot & macd,
      'R1_MACD':base & r1 & macd,
      'BEST_R1_MACD':base & best & r1 & macd,
      'BEST_R1_MACD_ACCEL':base & best & r1 & accel,
    }[v]


def backtest(symbol,weekly,v):
    b=build_bars(weekly); entry=entry_mask(b,v); pos=None; out=[]
    for i in range(len(b)-1):
        row=b.iloc[i]; nxt=b.iloc[i+1]
        if pos is not None and i>=pos['entry_idx']:
            reasons=_exit_reasons(row)
            if reasons and np.isfinite(float(nxt.open)):
                xp=float(nxt.open); ret=(xp/pos['entry_price']-1)*100
                out.append({'symbol':symbol,'variant':v,'entry_date':b.index[pos['entry_idx']].date().isoformat(),'exit_date':b.index[i+1].date().isoformat(),'return_pct':ret,'holding_weeks':i+1-pos['entry_idx'],'exit_reasons':'|'.join(reasons)})
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
    payload={'status':'SUCCESS','version':'AT_WEEKLY_IMPULSE_ENTRY_FILTER_V1','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':valid,'data_window':{'first_week':min(first).date().isoformat() if first else None,'last_week':max(last).date().isoformat() if last else None},'exit_rules_changed':False,'lookahead_controls':{'pivot_r1':'previous_completed_week_only','macd':'signal_week_close_only','execution':'next_week_open'},'entry_variants':{'BASELINE':'AT V1.1','CURRENT_BEST':'baseline + SMA20>SMA50 + close_location>=0.60','PIVOT':'baseline + close>previous-week pivot','R1':'baseline + close>previous-week R1','MACD':'baseline + MACD(12,26)>signal(9)','MACD_ACCEL':'baseline + MACD>signal + hist>0 + hist rising','PIVOT_MACD':'baseline + pivot + MACD','R1_MACD':'baseline + R1 + MACD','BEST_R1_MACD':'current best + R1 + MACD','BEST_R1_MACD_ACCEL':'current best + R1 + MACD accelerating'},'results':results,'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','RESEARCH_ONLY']}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); df.to_csv(OUT_CSV,sep=';',index=False,encoding='utf-8-sig'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
