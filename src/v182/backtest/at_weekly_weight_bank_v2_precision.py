"""Precision-focused second-stage weighted-entry search for weekly ACTION signals.
Research only. Exits remain exactly AT V1.1. Starts from the two V1 bank winners and
searches denser local weight neighborhoods with stricter score thresholds.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, math, random, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files, _iter_consolidated, CACHE_DIRS, MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import (
    FEATURES, RANGES, build_bars, feature_frame, future_outcomes,
    full_sim, summarize_full, max_score
)

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHT_BANK_V2_PRECISION.json'
OUT_TOP=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHT_BANK_V2_PRECISION_TOP.csv'
OUT_TRADES=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHT_BANK_V2_PRECISION_TRADES.csv'
SEED=20260829
THRESHOLDS=[.88,.90,.92,.94,.96,.98]
LOCAL_MODELS_PER_PARENT=1200
FULL_FINALISTS=20
FINAL_MIN_TRADES=50

PARENTS=[
 {'rsi':8,'stoch':6,'sma20':3,'sma50':1,'psar':2,'ma_trend':11,'close_loc':12,'r1':9,'macd':10,'breakout':28},
 {'rsi':5,'stoch':9,'sma20':5,'sma50':1,'psar':1,'ma_trend':8,'close_loc':8,'r1':10,'macd':6,'breakout':25},
]


def metrics_diag(ret,dates,sel):
    n=int(sel.sum())
    if n<50: return None
    rr=ret[sel]; dd=dates[sel]
    wr=float((rr>0).mean()*100); mean=float(rr.mean()); med=float(np.median(rr)); p10=float(np.quantile(rr,.10))
    pos=rr[rr>0].sum(); neg=-rr[rr<0].sum(); pf=float(pos/neg) if neg>0 else 99.0
    end=dates.max(); windows=[]
    for days,minn in [(365,15),(548,20),(730,30)]:
        r=rr[dd>=end-np.timedelta64(days,'D')]
        windows.append(float((r>0).mean()*100) if len(r)>=minn else 0.0)
    robust=min([wr]+windows)
    score=robust*5 + wr*2 + min(pf,4)*2 + max(-5,min(5,mean)) + min(n,300)/150
    return {'diag_score':round(score,4),'diag_trades':n,'diag_win_rate_pct':round(wr,2),
            'diag_wr12':round(windows[0],2),'diag_wr18':round(windows[1],2),'diag_wr24':round(windows[2],2),
            'diag_mean':round(mean,3),'diag_median':round(med,3),'diag_pf':round(pf,3),'diag_p10':round(p10,3)}


def perturb(parent,rng):
    w={}
    for k,v in parent.items():
        lo,hi=RANGES[k]
        span=5 if k in ('breakout','r1','macd','close_loc','ma_trend') else 3
        w[k]=max(lo,min(hi,v+rng.randint(-span,span)))
    return w


def run():
    st=time.perf_counter(); rng=random.Random(SEED)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    bars_map={}; rows=[]; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for symbol,hist,error in _iter_consolidated(path):
            if symbol is None or error or hist is None or hist.empty: continue
            w=_to_weekly(hist)
            if len(w)<MIN_WEEKLY_BARS: continue
            b=build_bars(w); bars_map[symbol]=b; first.append(b.index.min()); last.append(b.index.max())
            rows.extend(future_outcomes(symbol,b,feature_frame(b)))
    cols=['symbol','signal_ns','entry_ns','return_pct','holding_weeks']+FEATURES
    ev=pd.DataFrame(rows,columns=cols)
    X=ev[FEATURES].to_numpy(dtype=float); ret=ev.return_pct.to_numpy(dtype=float); dates=ev.signal_ns.to_numpy(dtype='datetime64[ns]')

    models=[]; seen=set()
    for pidx,parent in enumerate(PARENTS,1):
        for _ in range(LOCAL_MODELS_PER_PARENT):
            w=perturb(parent,rng)
            for th in THRESHOLDS:
                key=(tuple(w[k] for k in FEATURES),th)
                if key in seen: continue
                seen.add(key)
                ww=np.array([w[k] for k in FEATURES],dtype=float)
                sel=(X@ww)>=th*max_score(w)
                m=metrics_diag(ret,dates,sel)
                if m:
                    models.append({'parent':pidx,'weights':w,'threshold_ratio':th,**m})
    models.sort(key=lambda z:z['diag_score'],reverse=True)

    finalists=[]; sigs=set()
    for m in models:
        sig=(tuple(m['weights'][k] for k in FEATURES),m['threshold_ratio'])
        if sig in sigs: continue
        sigs.add(sig); finalists.append(m)
        if len(finalists)>=FULL_FINALISTS: break

    end=max(last); full=[]; all_tr=[]
    for i,m in enumerate(finalists,1):
        label=f'PREC_{i:02d}'; tr=[]
        for sym,b in bars_map.items(): tr.extend(full_sim(sym,b,m,label))
        df=pd.DataFrame(tr); sm=summarize_full(df,end)
        full.append({'label':label,**m,'full':sm}); all_tr.extend(tr)

    # Precision-first final ordering: robust win rate, then ALL win rate, then PF, with sample-size guard.
    def key(z):
        f=z['full']; a=f['ALL']
        vals=[f[k]['win_rate_pct'] for k in ['ALL','12M','18M','24M'] if f[k]['trades']>=15 and f[k]['win_rate_pct'] is not None]
        robust=min(vals) if vals else 0.0
        return (robust,a['win_rate_pct'] or 0,a['profit_factor'] or 0,min(a['trades'],300))
    full.sort(key=key,reverse=True)
    eligible=[z for z in full if z['full']['ALL']['trades']>=FINAL_MIN_TRADES]
    winners=eligible[:2] if len(eligible)>=2 else full[:2]

    top=[]
    for rank,z in enumerate(full,1):
        a=z['full']['ALL']; top.append({'rank':rank,'label':z['label'],'parent':z['parent'],'trades':a['trades'],
            'win_rate_pct':a['win_rate_pct'],'false_positive_pct':a['false_positive_pct'],'mean_return_pct':a['mean_return_pct'],
            'median_return_pct':a['median_return_pct'],'profit_factor':a['profit_factor'],'p10_return_pct':a['p10_return_pct'],
            'threshold_ratio':z['threshold_ratio'],**{f'w_{k}':z['weights'][k] for k in FEATURES}})
    pd.DataFrame(top).to_csv(OUT_TOP,sep=';',index=False,encoding='utf-8-sig')
    pd.DataFrame(all_tr).to_csv(OUT_TRADES,sep=';',index=False,encoding='utf-8-sig')
    payload={'status':'SUCCESS','version':'AT_WEEKLY_WEIGHT_BANK_V2_PRECISION','generated_at_utc':datetime.now(timezone.utc).isoformat(),
      'runtime_seconds':round(time.perf_counter()-st,3),'valid_actions':len(bars_map),'event_rows':len(ev),
      'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},
      'exit_rules_changed':False,'parents':PARENTS,'thresholds':THRESHOLDS,'local_models_per_parent':LOCAL_MODELS_PER_PARENT,
      'full_finalists':len(full),'final_two_models':winners,'top20':full,
      'selection_rule':'Precision-first: maximize worst win rate across ALL/12M/18M/24M, then ALL win rate and PF; minimum 50 trades preferred.',
      'lookahead_controls':{'pivot_r1':'previous_completed_week_only','breakout_highs':'prior_completed_weeks_only','macd':'signal_week_close_only','execution':'next_week_open'},
      'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','BROAD_STAGE_USES_INDEPENDENT_EVENT_OUTCOMES','FINALISTS_FULLY_RESIMULATED_ONE_POSITION_PER_SYMBOL','NO_FEES_SLIPPAGE','RESEARCH_ONLY','NO_EXIT_OPTIMISATION']}
    OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
