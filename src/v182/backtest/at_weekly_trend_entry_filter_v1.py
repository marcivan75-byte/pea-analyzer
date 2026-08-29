"""Compare AT weekly baseline vs entry-only moving-average trend filters for ACTIONS.
Research only. Exit rules remain identical to AT V1.1.
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

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "outputs/backtest/AT_WEEKLY_TREND_ENTRY_FILTER_V1.json"
OUT_CSV = ROOT / "outputs/backtest/AT_WEEKLY_TREND_ENTRY_FILTER_V1_TRADES.csv"


def metrics(df: pd.DataFrame):
    if df.empty:
        return {"trades":0,"win_rate_pct":None,"mean_return_pct":None,"median_return_pct":None,"profit_factor":None,"one_week_trades":0,"one_week_win_rate_pct":None,"one_week_mean_return_pct":None,"mean_holding_weeks":None}
    r=pd.to_numeric(df.return_pct,errors='coerce').dropna(); w=r[r>0]; l=r[r<0]
    pf=float(w.sum()/(-l.sum())) if len(l) and -l.sum()>0 else None
    one=df[pd.to_numeric(df.holding_weeks,errors='coerce')<=1]
    orr=pd.to_numeric(one.return_pct,errors='coerce').dropna()
    return {
      "trades":int(len(r)),"win_rate_pct":round(float((r>0).mean()*100),2),
      "mean_return_pct":round(float(r.mean()),3),"median_return_pct":round(float(r.median()),3),
      "profit_factor":round(pf,3) if pf is not None and math.isfinite(pf) else None,
      "one_week_trades":int(len(orr)),
      "one_week_win_rate_pct":round(float((orr>0).mean()*100),2) if len(orr) else None,
      "one_week_mean_return_pct":round(float(orr.mean()),3) if len(orr) else None,
      "mean_holding_weeks":round(float(pd.to_numeric(df.holding_weeks,errors='coerce').mean()),2),
    }


def backtest(symbol, weekly, variant):
    bars=_indicators(weekly)
    bars['ma20_gt_ma50']=bars['sma20']>bars['sma50']
    bars['ma20_slope_pos']=bars['sma20']>bars['sma20'].shift(1)
    base=bars['entry_signal'].fillna(False)
    if variant=='BASELINE': entry=base
    elif variant=='MA20_GT_MA50': entry=base & bars['ma20_gt_ma50'].fillna(False)
    elif variant=='MA20_SLOPE_POS': entry=base & bars['ma20_slope_pos'].fillna(False)
    elif variant=='TREND_DOUBLE': entry=base & bars['ma20_gt_ma50'].fillna(False) & bars['ma20_slope_pos'].fillna(False)
    else: raise ValueError(variant)
    pos=None; out=[]
    for i in range(len(bars)-1):
        row=bars.iloc[i]; nxt=bars.iloc[i+1]
        if pos is not None and i>=pos['entry_idx']:
            reasons=_exit_reasons(row)
            if reasons and np.isfinite(float(nxt.open)):
                xp=float(nxt.open); ret=(xp/pos['entry_price']-1)*100
                out.append({"symbol":symbol,"variant":variant,"entry_date":bars.index[pos['entry_idx']].date().isoformat(),"exit_date":bars.index[i+1].date().isoformat(),"return_pct":ret,"holding_weeks":i+1-pos['entry_idx'],"exit_reasons":"|".join(reasons)})
                pos=None; continue
        if pos is None and bool(entry.iloc[i]) and np.isfinite(float(nxt.open)):
            pos={"entry_idx":i+1,"entry_price":float(nxt.open)}
    return out, int(base.sum()), int(entry.sum())


def run():
    started=time.perf_counter(); variants=['BASELINE','MA20_GT_MA50','MA20_SLOPE_POS','TREND_DOUBLE']
    trades=[]; counts=Counter(); valid=0; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for symbol,hist,error in _iter_consolidated(path):
            if symbol is None or error or hist is None or hist.empty: continue
            weekly=_to_weekly(hist)
            if len(weekly)<MIN_WEEKLY_BARS: continue
            valid+=1; first.append(weekly.index.min()); last.append(weekly.index.max())
            for v in variants:
                t,b,s=backtest(symbol,weekly,v); trades.extend(t); counts[f'{v}_base_signals']+=b; counts[f'{v}_selected_signals']+=s
    df=pd.DataFrame(trades)
    if not df.empty: df=df.sort_values(['variant','entry_date','symbol'])
    end=max(last) if last else pd.Timestamp.utcnow().tz_localize(None)
    results={}
    for v in variants:
        sub=df[df.variant.eq(v)] if not df.empty else pd.DataFrame(columns=['return_pct','holding_weeks','entry_date'])
        vd={"ALL":metrics(sub)}
        entries=pd.to_datetime(sub.entry_date,errors='coerce') if not sub.empty else pd.Series(dtype='datetime64[ns]')
        for label,months in [('12M',12),('18M',18),('24M',24),('36M',36)]:
            cutoff=end-pd.DateOffset(months=months); vd[label]=metrics(sub[entries>=cutoff]) if not sub.empty else metrics(sub)
        vd['selected_entry_signals']=counts[f'{v}_selected_signals']; results[v]=vd
    payload={"status":"SUCCESS","version":"AT_WEEKLY_TREND_ENTRY_FILTER_V1","generated_at_utc":datetime.now(timezone.utc).isoformat(),"runtime_seconds":round(time.perf_counter()-started,3),"valid_actions":valid,"data_window":{"first_week":min(first).date().isoformat() if first else None,"last_week":max(last).date().isoformat() if last else None},"exit_rules_changed":False,"entry_variants":{"BASELINE":"AT V1.1","MA20_GT_MA50":"baseline + SMA20 > SMA50","MA20_SLOPE_POS":"baseline + SMA20[t] > SMA20[t-1]","TREND_DOUBLE":"baseline + SMA20 > SMA50 + pente SMA20 positive"},"results":results,"limitations":["CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP","SURVIVORSHIP_BIAS_POSSIBLE","NO_FEES_SLIPPAGE","RESEARCH_ONLY"]}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding='utf-8'); df.to_csv(OUT_CSV,sep=';',index=False,encoding='utf-8-sig'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
