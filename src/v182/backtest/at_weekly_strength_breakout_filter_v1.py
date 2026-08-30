"""AT weekly ACTION entry filters: cross-sectional relative strength and 10/20w breakout.
Research only. Exit rules remain exactly AT V1.1; execution is next weekly open.
Relative-strength benchmark is the PIT cross-sectional median ACTION return for the same week,
used only as a diagnostic proxy until an index-level historical benchmark series is available.
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
OUT=ROOT/'outputs/backtest/AT_WEEKLY_STRENGTH_BREAKOUT_FILTER_V1.json'
OUT_CSV=ROOT/'outputs/backtest/AT_WEEKLY_STRENGTH_BREAKOUT_FILTER_V1_TRADES.csv'
VARIANTS=[
 'REFERENCE_BEST_R1_MACD',
 'PLUS_NEAR_HIGH_10W','PLUS_BREAKOUT_10W','PLUS_NEAR_HIGH_20W','PLUS_BREAKOUT_20W',
 'PLUS_RS12','PLUS_RS4_RS12','PLUS_RS12_NEAR_HIGH_10W','PLUS_RS12_BREAKOUT_10W'
]


def metrics(df):
    if df.empty:
        return {'trades':0,'win_rate_pct':None,'mean_return_pct':None,'median_return_pct':None,'profit_factor':None,'p10_return_pct':None,'one_week_trades':0,'one_week_win_rate_pct':None,'one_week_mean_return_pct':None,'mean_holding_weeks':None}
    r=pd.to_numeric(df.return_pct,errors='coerce').dropna(); w=r[r>0]; l=r[r<0]
    pf=float(w.sum()/(-l.sum())) if len(l) and -l.sum()>0 else None
    one=df[pd.to_numeric(df.holding_weeks,errors='coerce')<=1]; rr=pd.to_numeric(one.return_pct,errors='coerce').dropna()
    return {'trades':int(len(r)),'win_rate_pct':round(float((r>0).mean()*100),2),'mean_return_pct':round(float(r.mean()),3),'median_return_pct':round(float(r.median()),3),'profit_factor':round(pf,3) if pf is not None and math.isfinite(pf) else None,'p10_return_pct':round(float(r.quantile(.10)),3),'one_week_trades':int(len(rr)),'one_week_win_rate_pct':round(float((rr>0).mean()*100),2) if len(rr) else None,'one_week_mean_return_pct':round(float(rr.mean()),3) if len(rr) else None,'mean_holding_weeks':round(float(pd.to_numeric(df.holding_weeks,errors='coerce').mean()),2)}


def build_bars(weekly, market_ret4, market_ret12):
    b=_indicators(weekly)
    ph=b['high'].shift(1); pl=b['low'].shift(1); pc=b['close'].shift(1)
    pivot=(ph+pl+pc)/3.0; b['r1_prev']=2.0*pivot-pl
    ema12=b['close'].ewm(span=12,adjust=False,min_periods=12).mean(); ema26=b['close'].ewm(span=26,adjust=False,min_periods=26).mean()
    b['macd']=ema12-ema26; b['macd_signal']=b['macd'].ewm(span=9,adjust=False,min_periods=9).mean()
    rng=(b['high']-b['low']).replace(0,np.nan); b['close_location']=(b['close']-b['low'])/rng
    b['ma20_gt_ma50']=b['sma20']>b['sma50']
    # Prior highs exclude the current signal week: strict PIT breakout reference.
    b['prior_high_10w']=b['high'].shift(1).rolling(10,min_periods=10).max()
    b['prior_high_20w']=b['high'].shift(1).rolling(20,min_periods=20).max()
    b['ret4']=b['close'].pct_change(4); b['ret12']=b['close'].pct_change(12)
    b['mkt_ret4']=market_ret4.reindex(b.index); b['mkt_ret12']=market_ret12.reindex(b.index)
    b['rs4']=b['ret4']-b['mkt_ret4']; b['rs12']=b['ret12']-b['mkt_ret12']
    return b


def entry_mask(b,v):
    base=b['entry_signal'].fillna(False)
    reference=base & b['ma20_gt_ma50'].fillna(False) & (b['close_location']>=.60).fillna(False) & (b['close']>b['r1_prev']).fillna(False) & (b['macd']>b['macd_signal']).fillna(False)
    near10=(b['close']>=.97*b['prior_high_10w']).fillna(False); brk10=(b['close']>b['prior_high_10w']).fillna(False)
    near20=(b['close']>=.97*b['prior_high_20w']).fillna(False); brk20=(b['close']>b['prior_high_20w']).fillna(False)
    rs12=(b['rs12']>0).fillna(False); rs4=(b['rs4']>0).fillna(False)
    return {
      'REFERENCE_BEST_R1_MACD':reference,
      'PLUS_NEAR_HIGH_10W':reference & near10,
      'PLUS_BREAKOUT_10W':reference & brk10,
      'PLUS_NEAR_HIGH_20W':reference & near20,
      'PLUS_BREAKOUT_20W':reference & brk20,
      'PLUS_RS12':reference & rs12,
      'PLUS_RS4_RS12':reference & rs4 & rs12,
      'PLUS_RS12_NEAR_HIGH_10W':reference & rs12 & near10,
      'PLUS_RS12_BREAKOUT_10W':reference & rs12 & brk10,
    }[v]


def backtest(symbol,weekly,v,market_ret4,market_ret12):
    b=build_bars(weekly,market_ret4,market_ret12); entry=entry_mask(b,v); pos=None; out=[]
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
    st=time.perf_counter(); weekly_map={}; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for symbol,hist,error in _iter_consolidated(path):
            if symbol is None or error or hist is None or hist.empty: continue
            w=_to_weekly(hist)
            if len(w)<MIN_WEEKLY_BARS: continue
            weekly_map[symbol]=w; first.append(w.index.min()); last.append(w.index.max())
    # PIT market proxy: each date's median return across the currently usable cache universe.
    ret4=pd.concat({s:w['close'].pct_change(4) for s,w in weekly_map.items()},axis=1).median(axis=1,skipna=True)
    ret12=pd.concat({s:w['close'].pct_change(12) for s,w in weekly_map.items()},axis=1).median(axis=1,skipna=True)
    trades=[]; counts=Counter()
    for symbol,w in weekly_map.items():
        for v in VARIANTS:
            t,n=backtest(symbol,w,v,ret4,ret12); trades.extend(t); counts[v]+=n
    df=pd.DataFrame(trades)
    if not df.empty: df=df.sort_values(['variant','entry_date','symbol'])
    end=max(last) if last else pd.Timestamp.utcnow().tz_localize(None); results={}
    for v in VARIANTS:
        sub=df[df.variant.eq(v)] if not df.empty else pd.DataFrame(columns=['return_pct','holding_weeks','entry_date'])
        vd={'ALL':metrics(sub)}; dates=pd.to_datetime(sub.entry_date,errors='coerce') if not sub.empty else pd.Series(dtype='datetime64[ns]')
        for lab,months in [('12M',12),('18M',18),('24M',24),('36M',36)]:
            cutoff=end-pd.DateOffset(months=months); vd[lab]=metrics(sub[dates>=cutoff]) if not sub.empty else metrics(sub)
        vd['selected_entry_signals']=int(counts[v]); results[v]=vd
    payload={'status':'SUCCESS','version':'AT_WEEKLY_STRENGTH_BREAKOUT_FILTER_V1','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(weekly_map),'data_window':{'first_week':min(first).date().isoformat() if first else None,'last_week':max(last).date().isoformat() if last else None},'exit_rules_changed':False,'reference':'AT V1.1 + SMA20>SMA50 + close_location>=0.60 + close>previous-week R1 + MACD>signal','lookahead_controls':{'breakout_highs':'prior_completed_weeks_only','relative_strength':'signal_week returns vs same-week cross-sectional median','execution':'next_week_open'},'relative_strength_note':'Diagnostic market proxy, not yet an index-specific benchmark. Do not promote RS criterion without index-level PIT confirmation.','entry_variants':{'PLUS_NEAR_HIGH_10W':'reference + close >= 97% prior 10w high','PLUS_BREAKOUT_10W':'reference + close > prior 10w high','PLUS_NEAR_HIGH_20W':'reference + close >= 97% prior 20w high','PLUS_BREAKOUT_20W':'reference + close > prior 20w high','PLUS_RS12':'reference + 12w return > market-median 12w return','PLUS_RS4_RS12':'reference + 4w and 12w returns > respective market medians','PLUS_RS12_NEAR_HIGH_10W':'reference + RS12 + near 10w high','PLUS_RS12_BREAKOUT_10W':'reference + RS12 + 10w breakout'},'results':results,'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','RS_USES_CROSS_SECTIONAL_PROXY_NOT_INDEX','NO_FEES_SLIPPAGE','RESEARCH_ONLY']}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); df.to_csv(OUT_CSV,sep=';',index=False,encoding='utf-8-sig'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
