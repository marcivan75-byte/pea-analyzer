"""Profile baseline AT weekly ACTION entries to identify false-positive separators.
Research-only diagnostic. No strategy rule is changed.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, math, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _indicators, _exit_reasons, _to_weekly
from .at_weekly_v1_fixed import _cache_files, _iter_consolidated, CACHE_DIRS, MIN_WEEKLY_BARS

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_ENTRY_PROFILE_V1.json'
OUT_CSV=ROOT/'outputs/backtest/AT_WEEKLY_ENTRY_PROFILE_V1_TRADES.csv'

FEATURES=['rsi14','stoch_k','stoch_d','dist_sma20_pct','dist_sma50_pct','ma_spread_pct','ret_4w_pct','ret_12w_pct','close_location','range_pct','body_pct','volume_ratio20','psar_dist_pct']


def fnum(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else np.nan
    except Exception:
        return np.nan


def enrich(b):
    b=b.copy()
    close=pd.to_numeric(b['close'],errors='coerce'); high=pd.to_numeric(b['high'],errors='coerce'); low=pd.to_numeric(b['low'],errors='coerce'); op=pd.to_numeric(b['open'],errors='coerce')
    b['dist_sma20_pct']=(close/b['sma20']-1)*100
    b['dist_sma50_pct']=(close/b['sma50']-1)*100
    b['ma_spread_pct']=(b['sma20']/b['sma50']-1)*100
    b['ret_4w_pct']=(close/close.shift(4)-1)*100
    b['ret_12w_pct']=(close/close.shift(12)-1)*100
    rng=(high-low).replace(0,np.nan)
    b['close_location']=(close-low)/rng
    b['range_pct']=rng/close*100
    b['body_pct']=(close-op).abs()/close*100
    if 'volume' in b.columns:
        vol=pd.to_numeric(b['volume'],errors='coerce'); b['volume_ratio20']=vol/vol.rolling(20,min_periods=20).mean()
    else: b['volume_ratio20']=np.nan
    b['psar_dist_pct']=(close/b['psar']-1)*100
    return b


def baseline_trades(symbol,weekly):
    bars=enrich(_indicators(weekly)); pos=None; out=[]
    for i in range(len(bars)-1):
        row=bars.iloc[i]; nxt=bars.iloc[i+1]
        if pos is not None and i>=pos['entry_idx']:
            reasons=_exit_reasons(row)
            if reasons and np.isfinite(fnum(nxt.open)):
                xp=fnum(nxt.open); ret=(xp/pos['entry_price']-1)*100
                rec={'symbol':symbol,'entry_date':bars.index[pos['entry_idx']].date().isoformat(),'exit_date':bars.index[i+1].date().isoformat(),'return_pct':ret,'holding_weeks':i+1-pos['entry_idx'],'exit_reasons':'|'.join(reasons)}
                rec.update(pos['features']); out.append(rec); pos=None; continue
        if pos is None and bool(row.get('entry_signal',False)) and np.isfinite(fnum(nxt.open)):
            feats={k:fnum(row.get(k,np.nan)) for k in FEATURES}
            pos={'entry_idx':i+1,'entry_price':fnum(nxt.open),'features':feats}
    return out


def stat(s):
    x=pd.to_numeric(s,errors='coerce').dropna()
    if x.empty:return {'n':0,'mean':None,'median':None,'q25':None,'q75':None}
    return {'n':int(len(x)),'mean':round(float(x.mean()),4),'median':round(float(x.median()),4),'q25':round(float(x.quantile(.25)),4),'q75':round(float(x.quantile(.75)),4)}


def group_metrics(df):
    if df.empty:return {'trades':0,'win_rate_pct':None,'mean_return_pct':None,'one_week_win_rate_pct':None}
    r=pd.to_numeric(df.return_pct,errors='coerce'); one=df[pd.to_numeric(df.holding_weeks,errors='coerce')<=1]; ro=pd.to_numeric(one.return_pct,errors='coerce')
    return {'trades':int(r.notna().sum()),'win_rate_pct':round(float((r>0).mean()*100),2),'mean_return_pct':round(float(r.mean()),3),'one_week_win_rate_pct':round(float((ro>0).mean()*100),2) if len(ro) else None}


def run():
    started=time.perf_counter(); rows=[]; valid=0; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for symbol,hist,error in _iter_consolidated(path):
            if symbol is None or error or hist is None or hist.empty:continue
            weekly=_to_weekly(hist)
            if len(weekly)<MIN_WEEKLY_BARS:continue
            valid+=1; first.append(weekly.index.min()); last.append(weekly.index.max()); rows.extend(baseline_trades(symbol,weekly))
    df=pd.DataFrame(rows)
    if not df.empty:df=df.sort_values(['entry_date','symbol'])
    win=df[pd.to_numeric(df.return_pct,errors='coerce')>0]; loss=df[pd.to_numeric(df.return_pct,errors='coerce')<=0]
    profile={}
    for f in FEATURES:
        profile[f]={'winners':stat(win[f]),'losers':stat(loss[f])}
    gates={}
    gate_defs={
      'MA20_GT_MA50': df['ma_spread_pct']>0,
      'RET4_GT_0': df['ret_4w_pct']>0,
      'RET12_GT_0': df['ret_12w_pct']>0,
      'CLOSE_LOC_GE_0_60': df['close_location']>=0.60,
      'CLOSE_LOC_GE_0_70': df['close_location']>=0.70,
      'VOL_RATIO20_GE_1': df['volume_ratio20']>=1.0,
      'PSAR_DIST_LE_10': df['psar_dist_pct']<=10,
      'DIST_MA20_LE_10': df['dist_sma20_pct']<=10,
    }
    for name,mask in gate_defs.items():gates[name]=group_metrics(df[mask.fillna(False)])
    payload={'status':'SUCCESS','version':'AT_WEEKLY_ENTRY_PROFILE_V1','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-started,3),'valid_actions':valid,'data_window':{'first_week':min(first).date().isoformat() if first else None,'last_week':max(last).date().isoformat() if last else None},'baseline':group_metrics(df),'feature_profiles':profile,'diagnostic_gates_on_baseline_trades':gates,'rules_changed':False,'limitations':['GATES_ARE_DIAGNOSTIC_SUBSETS_NOT_FULL_RESIMULATIONS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FEES_SLIPPAGE','RESEARCH_ONLY']}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); df.to_csv(OUT_CSV,sep=';',index=False,encoding='utf-8-sig'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__':run()
