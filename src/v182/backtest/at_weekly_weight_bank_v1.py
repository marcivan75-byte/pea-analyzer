"""Comprehensive weighted-entry test bank for weekly ACTION signals.
Research only. Entry criteria are scored (not stacked as an AND gate); exits remain exactly AT V1.1.
Two-stage search: broad deterministic random bank -> full re-simulation of finalists -> local optimisation of top 2.
No production promotion. Signal at completed weekly close, execution at next weekly open.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, math, random, time
import numpy as np
import pandas as pd

from .at_weekly_v1 import _indicators, _exit_reasons, _to_weekly
from .at_weekly_v1_fixed import _cache_files, _iter_consolidated, CACHE_DIRS, MIN_WEEKLY_BARS

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHT_BANK_V1.json'
OUT_BANK=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHT_BANK_V1_TOP.csv'
OUT_TRADES=ROOT/'outputs/backtest/AT_WEEKLY_WEIGHT_BANK_V1_FINAL_TRADES.csv'
SEED=20260829
FEATURES=['rsi','stoch','sma20','sma50','psar','ma_trend','close_loc','r1','macd','breakout']
# ranges are deliberately broad; strong prior ranges for R1/MACD/breakout reflect prior diagnostics.
RANGES={
 'rsi':(1,8),'stoch':(1,12),'sma20':(1,8),'sma50':(1,10),'psar':(1,10),
 'ma_trend':(2,14),'close_loc':(2,14),'r1':(4,20),'macd':(4,20),'breakout':(8,28),
}
THRESHOLDS=[.55,.62,.69,.76,.83]
BROAD_MODELS=700
FULL_FINALISTS=14
LOCAL_NEIGHBORS=36
LOCAL_FULL_PER_PARENT=7


def metrics(df):
    if df.empty:
        return {'trades':0,'win_rate_pct':None,'false_positive_pct':None,'mean_return_pct':None,'median_return_pct':None,'profit_factor':None,'p10_return_pct':None,'one_week_trades':0,'one_week_win_rate_pct':None,'one_week_mean_return_pct':None,'mean_holding_weeks':None}
    r=pd.to_numeric(df.return_pct,errors='coerce').dropna(); w=r[r>0]; l=r[r<0]
    pf=float(w.sum()/(-l.sum())) if len(l) and -l.sum()>0 else None; wr=float((r>0).mean()*100)
    one=df[pd.to_numeric(df.holding_weeks,errors='coerce')<=1]; rr=pd.to_numeric(one.return_pct,errors='coerce').dropna()
    return {'trades':int(len(r)),'win_rate_pct':round(wr,2),'false_positive_pct':round(100-wr,2),
      'mean_return_pct':round(float(r.mean()),3),'median_return_pct':round(float(r.median()),3),
      'profit_factor':round(pf,3) if pf is not None and math.isfinite(pf) else None,'p10_return_pct':round(float(r.quantile(.10)),3),
      'one_week_trades':int(len(rr)),'one_week_win_rate_pct':round(float((rr>0).mean()*100),2) if len(rr) else None,
      'one_week_mean_return_pct':round(float(rr.mean()),3) if len(rr) else None,
      'mean_holding_weeks':round(float(pd.to_numeric(df.holding_weeks,errors='coerce').mean()),2)}


def build_bars(w):
    b=_indicators(w)
    ph=b.high.shift(1); pl=b.low.shift(1); pc=b.close.shift(1); pivot=(ph+pl+pc)/3.0
    b['r1_prev']=2*pivot-pl
    e12=b.close.ewm(span=12,adjust=False,min_periods=12).mean(); e26=b.close.ewm(span=26,adjust=False,min_periods=26).mean()
    b['macd']=e12-e26; b['macd_signal']=b.macd.ewm(span=9,adjust=False,min_periods=9).mean()
    rng=(b.high-b.low).replace(0,np.nan); b['close_location']=(b.close-b.low)/rng
    b['h10']=b.high.shift(1).rolling(10,min_periods=10).max(); b['h20']=b.high.shift(1).rolling(20,min_periods=20).max()
    return b


def feature_frame(b):
    x=pd.DataFrame(index=b.index)
    x['rsi']=(b.rsi14<60).astype(float)
    x['stoch']=b.stoch_cross_up.fillna(False).astype(float)
    x['sma20']=(b.close>b.sma20).fillna(False).astype(float)
    x['sma50']=(b.close>b.sma50).fillna(False).astype(float)
    x['psar']=(b.close>b.psar).fillna(False).astype(float)
    x['ma_trend']=(b.sma20>b.sma50).fillna(False).astype(float)
    x['close_loc']=(b.close_location>=.60).fillna(False).astype(float)
    x['r1']=(b.close>b.r1_prev).fillna(False).astype(float)
    x['macd']=(b.macd>b.macd_signal).fillna(False).astype(float)
    # hierarchical 0..1 breakout strength; strongest condition replaces weaker nested states.
    state=np.zeros(len(b),dtype=float)
    near10=(b.close>=.97*b.h10).fillna(False).to_numpy(); near20=(b.close>=.97*b.h20).fillna(False).to_numpy()
    br10=(b.close>b.h10).fillna(False).to_numpy(); br20=(b.close>b.h20).fillna(False).to_numpy()
    state[near10]=.25; state[near20]=np.maximum(state[near20],.50); state[br10]=np.maximum(state[br10],.75); state[br20]=1.0
    x['breakout']=state
    return x


def future_outcomes(symbol,b,x):
    # Independent-event outcomes for broad ranking. Full finalists are re-simulated with one-position logic later.
    n=len(b); next_exit=np.full(n,-1,dtype=int); nxt=-1
    for i in range(n-2,-1,-1):
        if _exit_reasons(b.iloc[i]): nxt=i
        next_exit[i]=nxt
    rows=[]
    valid_cols=['rsi14','stoch_k','stoch_d','sma20','sma50','psar','macd','macd_signal','r1_prev','h10','h20']
    ok=b[valid_cols].notna().all(axis=1).to_numpy()
    for i in range(n-2):
        j=next_exit[i+1]
        if not ok[i] or j<0 or j+1>=n: continue
        ep=float(b.open.iloc[i+1]); xp=float(b.open.iloc[j+1])
        if not (np.isfinite(ep) and np.isfinite(xp) and ep>0): continue
        r=(xp/ep-1)*100
        rows.append([symbol,b.index[i].value,b.index[i+1].value,r,j+1-(i+1),*x.iloc[i][FEATURES].to_numpy(dtype=float)])
    return rows


def draw_weights(rng): return {k:rng.randint(*RANGES[k]) for k in FEATURES}
def max_score(w): return sum(w[k] for k in FEATURES[:-1])+w['breakout']

def diag_rank(X,ret,dates,w,thr):
    ww=np.array([w[k] for k in FEATURES],dtype=float); s=X@ww; sel=s>=thr*max_score(w); n=int(sel.sum())
    if n<70: return None
    rr=ret[sel]; wr=float((rr>0).mean()*100); mean=float(rr.mean()); med=float(np.median(rr)); p10=float(np.quantile(rr,.10))
    pos=rr[rr>0].sum(); neg=-rr[rr<0].sum(); pf=float(pos/neg) if neg>0 else 99.0
    dsel=dates[sel]; cutoff12=dates.max()-np.timedelta64(365,'D'); cutoff24=dates.max()-np.timedelta64(730,'D')
    r12=rr[dsel>=cutoff12]; r24=rr[dsel>=cutoff24]
    wr12=float((r12>0).mean()*100) if len(r12)>=15 else 0.0; wr24=float((r24>0).mean()*100) if len(r24)>=30 else 0.0
    robust=min(wr,wr12,wr24)
    # false-positive objective dominates; PF/mean/p10 are tie-breakers; sample-size reward is capped.
    score=robust*3 + wr + min(pf,4)*3 + max(-5,min(5,mean))*1.5 + max(-10,p10)*.2 + min(n,250)/100
    return {'diag_score':round(score,4),'diag_trades':n,'diag_win_rate_pct':round(wr,2),'diag_wr12':round(wr12,2),'diag_wr24':round(wr24,2),
      'diag_mean':round(mean,3),'diag_median':round(med,3),'diag_pf':round(pf,3),'diag_p10':round(p10,3),'threshold_ratio':thr,'weights':w}


def entry_mask_from_model(b,w,thr):
    x=feature_frame(b); score=sum(x[k]*w[k] for k in FEATURES); return (score>=thr*max_score(w)).fillna(False)


def full_sim(symbol,b,model,label):
    entry=entry_mask_from_model(b,model['weights'],model['threshold_ratio']); pos=None; out=[]
    for i in range(len(b)-1):
        row=b.iloc[i]; nxt=b.iloc[i+1]
        if pos is not None and i>=pos['entry_idx']:
            reasons=_exit_reasons(row)
            if reasons and np.isfinite(float(nxt.open)):
                xp=float(nxt.open); out.append({'symbol':symbol,'model':label,'entry_date':b.index[pos['entry_idx']].date().isoformat(),
                  'exit_date':b.index[i+1].date().isoformat(),'return_pct':(xp/pos['entry_price']-1)*100,'holding_weeks':i+1-pos['entry_idx']})
                pos=None; continue
        if pos is None and bool(entry.iloc[i]) and np.isfinite(float(nxt.open)) and float(nxt.open)>0:
            pos={'entry_idx':i+1,'entry_price':float(nxt.open)}
    return out


def summarize_full(df,end):
    res={'ALL':metrics(df)}; d=pd.to_datetime(df.entry_date,errors='coerce') if not df.empty else pd.Series(dtype='datetime64[ns]')
    for lab,m in [('12M',12),('18M',18),('24M',24),('36M',36)]:
        res[lab]=metrics(df[d>=end-pd.DateOffset(months=m)]) if not df.empty else metrics(df)
    a=res['ALL']; vals=[res[k]['win_rate_pct'] for k in ['ALL','12M','18M','24M'] if res[k]['trades']>=15 and res[k]['win_rate_pct'] is not None]
    robust=min(vals) if vals else 0.0; pf=a['profit_factor'] or 0.0
    res['selection_score']=round(robust*4+(a['win_rate_pct'] or 0)+min(float(pf),4)*3+min(a['trades'],250)/100,4)
    return res


def local_models(parent,rng):
    out=[]
    for _ in range(LOCAL_NEIGHBORS):
        w={}
        for k,v in parent['weights'].items():
            lo,hi=RANGES[k]; w[k]=max(lo,min(hi,v+rng.choice([-3,-2,-1,0,1,2,3])))
        t=max(.50,min(.90,parent['threshold_ratio']+rng.choice([-.04,-.025,-.015,0,.015,.025,.04])))
        out.append({'weights':w,'threshold_ratio':round(t,3)})
    return out


def run():
    st=time.perf_counter(); rng=random.Random(SEED); bars_map={}; event_rows=[]; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for symbol,hist,error in _iter_consolidated(path):
            if symbol is None or error or hist is None or hist.empty: continue
            w=_to_weekly(hist)
            if len(w)<MIN_WEEKLY_BARS: continue
            b=build_bars(w); bars_map[symbol]=b; first.append(b.index.min()); last.append(b.index.max())
            event_rows.extend(future_outcomes(symbol,b,feature_frame(b)))
    cols=['symbol','signal_ns','entry_ns','return_pct','holding_weeks']+FEATURES
    ev=pd.DataFrame(event_rows,columns=cols); X=ev[FEATURES].to_numpy(dtype=float); ret=ev.return_pct.to_numpy(dtype=float); dates=ev.signal_ns.to_numpy(dtype='datetime64[ns]')
    bank=[]
    # Include two interpretable anchors then broad deterministic random search.
    anchors=[
      {'rsi':3,'stoch':7,'sma20':3,'sma50':4,'psar':4,'ma_trend':7,'close_loc':7,'r1':12,'macd':12,'breakout':22},
      {'rsi':2,'stoch':6,'sma20':2,'sma50':3,'psar':3,'ma_trend':6,'close_loc':8,'r1':14,'macd':14,'breakout':28},
    ]
    weight_sets=anchors+[draw_weights(rng) for _ in range(BROAD_MODELS)]
    for w in weight_sets:
        for th in THRESHOLDS:
            z=diag_rank(X,ret,dates,w,th)
            if z: bank.append(z)
    bank=sorted(bank,key=lambda z:z['diag_score'],reverse=True)
    # Deduplicate by weights+threshold and take broad finalists.
    seen=set(); finalists=[]
    for z in bank:
        key=(tuple(z['weights'][k] for k in FEATURES),z['threshold_ratio'])
        if key in seen: continue
        seen.add(key); finalists.append(z)
        if len(finalists)>=FULL_FINALISTS: break
    end=max(last); full=[]; all_tr=[]
    for idx,m in enumerate(finalists):
        label=f'BROAD_{idx+1:02d}'; tr=[]
        for sym,b in bars_map.items(): tr.extend(full_sim(sym,b,m,label))
        df=pd.DataFrame(tr); sm=summarize_full(df,end); full.append({'label':label,**m,'full':sm}); all_tr.extend(tr)
    full=sorted(full,key=lambda z:z['full']['selection_score'],reverse=True); parents=full[:2]
    # Local optimisation around each of the two strongest full-resimulated parents.
    local_full=[]
    for pidx,p in enumerate(parents,1):
        local=[]
        for m in local_models(p,rng):
            z=diag_rank(X,ret,dates,m['weights'],m['threshold_ratio'])
            if z: local.append(z)
        local=sorted(local,key=lambda z:z['diag_score'],reverse=True)[:LOCAL_FULL_PER_PARENT]
        for j,m in enumerate(local,1):
            label=f'P{pidx}_LOCAL_{j:02d}'; tr=[]
            for sym,b in bars_map.items(): tr.extend(full_sim(sym,b,m,label))
            df=pd.DataFrame(tr); sm=summarize_full(df,end); local_full.append({'label':label,**m,'full':sm}); all_tr.extend(tr)
    combined=sorted(full+local_full,key=lambda z:z['full']['selection_score'],reverse=True)
    # Keep two final models structurally distinct if possible.
    winners=[]
    for z in combined:
        if z['full']['ALL']['trades']<50: continue
        if not winners: winners.append(z); continue
        dist=sum(abs(z['weights'][k]-winners[0]['weights'][k]) for k in FEATURES)
        if dist>=10 or abs(z['threshold_ratio']-winners[0]['threshold_ratio'])>=.04: winners.append(z); break
    if len(winners)<2: winners=combined[:2]
    top_rows=[]
    for rank,z in enumerate(combined[:30],1):
        a=z['full']['ALL']; top_rows.append({'rank':rank,'label':z['label'],'selection_score':z['full']['selection_score'],'trades':a['trades'],
          'win_rate_pct':a['win_rate_pct'],'false_positive_pct':a['false_positive_pct'],'mean_return_pct':a['mean_return_pct'],'profit_factor':a['profit_factor'],
          'p10_return_pct':a['p10_return_pct'],'threshold_ratio':z['threshold_ratio'],**{f'w_{k}':z['weights'][k] for k in FEATURES}})
    pd.DataFrame(top_rows).to_csv(OUT_BANK,sep=';',index=False,encoding='utf-8-sig')
    tdf=pd.DataFrame(all_tr); tdf.to_csv(OUT_TRADES,sep=';',index=False,encoding='utf-8-sig')
    payload={'status':'SUCCESS','version':'AT_WEEKLY_WEIGHT_BANK_V1','generated_at_utc':datetime.now(timezone.utc).isoformat(),'runtime_seconds':round(time.perf_counter()-st,3),
      'valid_actions':len(bars_map),'event_rows':len(ev),'data_window':{'first_week':min(first).date().isoformat(),'last_week':max(last).date().isoformat()},
      'exit_rules_changed':False,'entry_mode':'FULL_WEIGHTED_SCORE_ALL_CURRENT_CRITERIA_PLUS_BREAKOUT','target_false_positive_pct':30.0,
      'features':FEATURES,'weight_ranges':RANGES,'broad_models_requested':BROAD_MODELS,'thresholds':THRESHOLDS,'full_finalists':len(full),
      'two_parent_models_before_local_optimisation':parents,'final_two_models':winners,'top10':combined[:10],
      'selection_rule':'False-positive reduction dominates via worst win-rate across ALL/12M/18M/24M; PF and sample size are secondary. Minimum 50 trades for final preference.',
      'lookahead_controls':{'pivot_r1':'previous_completed_week_only','breakout_highs':'prior_completed_weeks_only','macd':'signal_week_close_only','execution':'next_week_open'},
      'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','BROAD_STAGE_USES_INDEPENDENT_EVENT_OUTCOMES','FINALISTS_FULLY_RESIMULATED_ONE_POSITION_PER_SYMBOL','NO_FEES_SLIPPAGE','RESEARCH_ONLY','NO_EXIT_OPTIMISATION']}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(payload,indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
