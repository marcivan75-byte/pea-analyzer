"""V3 continuous-strength entry scoring for weekly ACTION signals.
Research only. Same indicators and AT V1.1 exits; next-week-open execution.
Purpose: break the binary-score selection plateau observed in V2 without adding new indicators.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, random, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _exit_reasons, _to_weekly
from .at_weekly_v1_fixed import _cache_files, _iter_consolidated, CACHE_DIRS, MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars, metrics

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHT_BANK_V3_CONTINUOUS.json'
OUT_TOP=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHT_BANK_V3_CONTINUOUS_TOP.csv'
OUT_TRADES=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHT_BANK_V3_CONTINUOUS_TRADES.csv'
SEED=20260829
FEATURES=['rsi','stoch','sma20','sma50','psar','ma_trend','close_loc','r1','macd','breakout','br_macd','br_r1','trend_close']
RANGES={
 'rsi':(1,8),'stoch':(2,12),'sma20':(1,8),'sma50':(1,8),'psar':(1,8),
 'ma_trend':(4,18),'close_loc':(4,18),'r1':(5,20),'macd':(5,20),'breakout':(12,32),
 'br_macd':(4,22),'br_r1':(4,22),'trend_close':(3,18),
}
THRESHOLDS=[.58,.64,.70,.76,.82,.88,.92]
MODELS=1200
FULL_FINALISTS=18
MIN_DIAG=60
MIN_FINAL=50


def _clip(v,lo=0.0,hi=1.0):
    return np.clip(v,lo,hi)


def strength_frame(b):
    """Bounded 0..1 strengths. Fixed transforms are pre-specified, not fitted to outcomes."""
    x=pd.DataFrame(index=b.index)
    # Keep weakly-discriminating RSI as the original condition; do not invent a fitted optimum.
    x['rsi']=(b.rsi14<60).fillna(False).astype(float)
    gap=((b.stoch_k-b.stoch_d)/15.0).clip(lower=0,upper=1).fillna(0)
    x['stoch']=np.where(b.stoch_cross_up.fillna(False),0.5+0.5*gap,0.0)

    d20=(b.close/b.sma20-1).replace([np.inf,-np.inf],np.nan)
    d50=(b.close/b.sma50-1).replace([np.inf,-np.inf],np.nan)
    dps=(b.close/b.psar-1).replace([np.inf,-np.inf],np.nan)
    dtrend=(b.sma20/b.sma50-1).replace([np.inf,-np.inf],np.nan)
    dr1=(b.close/b.r1_prev-1).replace([np.inf,-np.inf],np.nan)
    hist=(b.macd-b.macd_signal)/b.close.replace(0,np.nan)

    x['sma20']=np.where(d20>0,0.5+0.5*_clip(d20/0.08),0.0)
    x['sma50']=np.where(d50>0,0.5+0.5*_clip(d50/0.12),0.0)
    x['psar']=np.where(dps>0,0.5+0.5*_clip(dps/0.10),0.0)
    x['ma_trend']=np.where(dtrend>0,0.5+0.5*_clip(dtrend/0.08),0.0)
    x['close_loc']=_clip((b.close_location.fillna(0)-0.50)/0.50)
    x['r1']=np.where(dr1>0,0.5+0.5*_clip(dr1/0.08),0.0)
    x['macd']=np.where(hist>0,0.5+0.5*_clip(hist/0.03),0.0)

    r10=(b.close/b.h10).replace([np.inf,-np.inf],np.nan)
    r20=(b.close/b.h20).replace([np.inf,-np.inf],np.nan)
    # Blend proximity/excess versus both 10w and 20w prior highs; continuous around the old 0.97/breakout states.
    br10=_clip((r10.fillna(0)-0.94)/0.12)
    br20=_clip((r20.fillna(0)-0.94)/0.12)
    x['breakout']=0.5*br10+0.5*br20
    x['br_macd']=x['breakout']*x['macd']
    x['br_r1']=x['breakout']*x['r1']
    x['trend_close']=x['ma_trend']*x['close_loc']
    return x[FEATURES].astype(float)


def future_outcomes(symbol,b,x):
    n=len(b); next_exit=np.full(n,-1,dtype=int); nxt=-1
    for i in range(n-2,-1,-1):
        if _exit_reasons(b.iloc[i]): nxt=i
        next_exit[i]=nxt
    valid=b[['rsi14','stoch_k','stoch_d','sma20','sma50','psar','macd','macd_signal','r1_prev','h10','h20']].notna().all(axis=1).to_numpy()
    rows=[]
    for i in range(n-2):
        j=next_exit[i+1]
        if not valid[i] or j<0 or j+1>=n: continue
        ep=float(b.open.iloc[i+1]); xp=float(b.open.iloc[j+1])
        if not(np.isfinite(ep) and np.isfinite(xp) and ep>0): continue
        rows.append([symbol,b.index[i].value,(xp/ep-1)*100,j+1-(i+1),*x.iloc[i].to_numpy(dtype=float)])
    return rows


def draw_weights(rng): return {k:rng.randint(*RANGES[k]) for k in FEATURES}
def max_score(w): return float(sum(w.values()))


def diag(ret,dates,X,w,thr):
    ww=np.array([w[k] for k in FEATURES],dtype=float); sel=(X@ww)>=thr*max_score(w); n=int(sel.sum())
    if n<MIN_DIAG: return None
    rr=ret[sel]; dd=dates[sel]; end=dates.max(); wr=float((rr>0).mean()*100)
    windows=[]
    for days,minn in [(365,15),(548,20),(730,30)]:
        z=rr[dd>=end-np.timedelta64(days,'D')]
        windows.append(float((z>0).mean()*100) if len(z)>=minn else 0.0)
    robust=min([wr]+windows); pos=rr[rr>0].sum(); neg=-rr[rr<0].sum(); pf=float(pos/neg) if neg>0 else 99.0
    mean=float(rr.mean()); p10=float(np.quantile(rr,.10))
    score=robust*6+wr*2+min(pf,4)*2+max(-4,min(4,mean))+max(-8,p10)*.15+min(n,250)/125
    return {'diag_score':round(score,4),'diag_trades':n,'diag_win_rate_pct':round(wr,2),'diag_wr12':round(windows[0],2),'diag_wr18':round(windows[1],2),'diag_wr24':round(windows[2],2),'diag_mean':round(mean,3),'diag_pf':round(pf,3),'diag_p10':round(p10,3)}


def entry_mask(b,w,thr):
    x=strength_frame(b); s=sum(x[k]*w[k] for k in FEATURES); return (s>=thr*max_score(w)).fillna(False)


def full_sim(symbol,b,m,label):
    entry=entry_mask(b,m['weights'],m['threshold_ratio']); pos=None; out=[]
    for i in range(len(b)-1):
        row=b.iloc[i]; nxt=b.iloc[i+1]
        if pos is not None and i>=pos['idx']:
            if _exit_reasons(row) and np.isfinite(float(nxt.open)):
                xp=float(nxt.open); out.append({'symbol':symbol,'model':label,'entry_date':b.index[pos['idx']].date().isoformat(),'exit_date':b.index[i+1].date().isoformat(),'return_pct':(xp/pos['price']-1)*100,'holding_weeks':i+1-pos['idx']}); pos=None; continue
        if pos is None and bool(entry.iloc[i]) and np.isfinite(float(nxt.open)) and float(nxt.open)>0:
            pos={'idx':i+1,'price':float(nxt.open)}
    return out


def summarize(df,end):
    res={'ALL':metrics(df)}; d=pd.to_datetime(df.entry_date,errors='coerce') if not df.empty else pd.Series(dtype='datetime64[ns]')
    for lab,m in [('12M',12),('18M',18),('24M',24),('36M',36)]: res[lab]=metrics(df[d>=end-pd.DateOffset(months=m)]) if not df.empty else metrics(df)
    vals=[res[k]['win_rate_pct'] for k in ['ALL','12M','18M','24M'] if res[k]['trades']>=15 and res[k]['win_rate_pct'] is not None]
    robust=min(vals) if vals else 0.0; a=res['ALL']; res['selection_key']=[robust,a['win_rate_pct'] or 0,a['profit_factor'] or 0,min(a['trades'],250)]
    return res


def run():
    st=time.perf_counter(); rng=random.Random(SEED); OUT.parent.mkdir(parents=True,exist_ok=True)
    bars={}; rows=[]; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for symbol,hist,error in _iter_consolidated(path):
            if symbol is None or error or hist is None or hist.empty: continue
            w=_to_weekly(hist)
            if len(w)<MIN_WEEKLY_BARS: continue
            b=build_bars(w); bars[symbol]=b; first.append(b.index.min()); last.append(b.index.max()); rows.extend(future_outcomes(symbol,b,strength_frame(b)))
    cols=['symbol','signal_ns','return_pct','holding_weeks']+FEATURES; ev=pd.DataFrame(rows,columns=cols)
    X=ev[FEATURES].to_numpy(float); ret=ev.return_pct.to_numpy(float); dates=ev.signal_ns.to_numpy(dtype='datetime64[ns]')

    # Include two interpretable anchors around V2's importance ordering, then deterministic random bank.
    anchors=[
      {'rsi':5,'stoch':7,'sma20':3,'sma50':1,'psar':2,'ma_trend':12,'close_loc':14,'r1':10,'macd':10,'breakout':30,'br_macd':14,'br_r1':14,'trend_close':10},
      {'rsi':4,'stoch':8,'sma20':4,'sma50':1,'psar':2,'ma_trend':10,'close_loc':12,'r1':12,'macd':9,'breakout':28,'br_macd':16,'br_r1':16,'trend_close':9},
    ]
    bank=[]; seen=set()
    for w in anchors+[draw_weights(rng) for _ in range(MODELS)]:
        for th in THRESHOLDS:
            key=(tuple(w[k] for k in FEATURES),th)
            if key in seen: continue
            seen.add(key); z=diag(ret,dates,X,w,th)
            if z: bank.append({'weights':w,'threshold_ratio':th,**z})
    bank.sort(key=lambda z:z['diag_score'],reverse=True)
    finalists=bank[:FULL_FINALISTS]; end=max(last); full=[]; all_tr=[]
    for i,m in enumerate(finalists,1):
        label=f'CONT_{i:02d}'; tr=[]
        for sym,b in bars.items(): tr.extend(full_sim(sym,b,m,label))
        df=pd.DataFrame(tr); sm=summarize(df,end); full.append({'label':label,**m,'full':sm}); all_tr.extend(tr)
    full.sort(key=lambda z:tuple(z['full']['selection_key']),reverse=True)
    eligible=[z for z in full if z['full']['ALL']['trades']>=MIN_FINAL]; winners=eligible[:2] if len(eligible)>=2 else full[:2]
    top=[]
    for rank,z in enumerate(full,1):
        a=z['full']['ALL']; top.append({'rank':rank,'label':z['label'],'trades':a['trades'],'win_rate_pct':a['win_rate_pct'],'false_positive_pct':a['false_positive_pct'],'mean_return_pct':a['mean_return_pct'],'median_return_pct':a['median_return_pct'],'profit_factor':a['profit_factor'],'p10_return_pct':a['p10_return_pct'],'threshold_ratio':z['threshold_ratio'],**{f'w_{k}':z['weights'][k] for k in FEATURES}})
    pd.DataFrame(top).to_csv(OUT_TOP,sep=';',index=False,encoding='utf-8-sig'); pd.DataFrame(all_tr).to_csv(OUT_TRADES,sep=';',index=False,encoding='utf-8-sig')
    payload={'status':'SUCCESS','version':'AT_WEEKLY_WEIGHT_BANK_V3_CONTINUOUS','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars),'event_rows':len(ev),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},'exit_rules_changed':False,'features':FEATURES,'thresholds':THRESHOLDS,'models_requested':MODELS,'full_finalists':len(full),'final_two_models':winners,'top':full,'selection_rule':'Precision-first worst win rate across ALL/12M/18M/24M, then ALL win/PF, minimum 50 trades preferred.','method':'continuous fixed-strength transforms plus interaction bonuses; no new market indicator','lookahead_controls':{'pivot_r1':'previous_completed_week_only','breakout_highs':'prior_completed_weeks_only','macd':'signal_week_close_only','execution':'next_week_open'},'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','FIXED_CONTINUOUS_SCALINGS_NOT_OUTCOME_FITTED','INTERACTIONS_RESEARCH_ONLY','NO_FEES_SLIPPAGE','RESEARCH_ONLY','NO_EXIT_OPTIMISATION']}
    OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
