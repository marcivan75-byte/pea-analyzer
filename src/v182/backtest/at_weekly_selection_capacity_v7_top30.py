"""Research-only V7: capacity-constrained PIT entry selection.

User constraint translated into an executable no-lookahead rule: at most 30 executed
entries per calendar year. We cannot select ex ante on the future fact that a trade
will reach take-profit; instead we rank only with completed-week PIT features and then
measure D-reversal take-profit outcomes afterward.

Locked entry/exit architecture is reused unchanged. The study tests a small,
pre-declared hardening ladder, not a broad fitted grid. Within each signal week,
candidates are ranked by an ex-ante quality/potential score; at most one candidate is
admitted per week and the annual capacity stops at 30. Future signals in the same year
are never used to rank an earlier decision.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_growth_potential_pit_v1 import enrich_trades, summarize_bucket, clip_score
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_SELECTION_CAPACITY_V7_TOP30.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_SELECTION_CAPACITY_V7_TOP30.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_SELECTION_CAPACITY_V7_TOP30_TRADES.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_SELECTION_CAPACITY_V7_TOP30.md'
ANNUAL_CAP=30
WEEKLY_CAP=1

# Small pre-declared hardening ladder. Thresholds are intentionally simple and are
# not optimized against outcomes inside this script.
CONFIGS=[
    {'name':'A_MOM40','m12':40.0},
    {'name':'B_MOM40_M26_20_ACCEL0','m12':40.0,'m26':20.0,'accel4':0.0},
    {'name':'C_B_PLUS_TREND','m12':40.0,'m26':20.0,'accel4':0.0,'d20':0.0,'d50':0.0},
    {'name':'D_C_PLUS_TIMING','m12':40.0,'m26':20.0,'accel4':0.0,'d20':0.0,'d50':0.0,'rsi_max':70.0,'stoch_max':85.0},
]


def potential_score(r):
    # 100-point ex-ante ranking score, fixed before outcome evaluation.
    # Emphasizes persistent momentum and acceleration while rewarding trend quality
    # and avoiding extreme oscillator heat.
    m12=r.get('momentum_12w_pct',np.nan); m26=r.get('momentum_26w_pct',np.nan)
    a4=r.get('acceleration_4w_pct',np.nan); d20=r.get('distance_sma20_pct',np.nan)
    d50=r.get('distance_sma50_pct',np.nan); br=r.get('breakout_above_prior_52w_high_pct',np.nan)
    rsi=r.get('rsi14',np.nan); st=r.get('stoch_k',np.nan)
    score=(clip_score(m12,20,80,30)+clip_score(m26,10,100,20)+clip_score(a4,-5,20,15)+
           clip_score(br,0,15,10)+clip_score(d20,0,20,10)+clip_score(d50,0,35,10))
    # Timing quality: full 5 pts in a healthy-but-not-overheated zone, linearly less
    # outside it. No future outcome is referenced.
    timing=0.0
    if np.isfinite(rsi) and np.isfinite(st):
        if 45<=rsi<=65 and 45<=st<=80:
            timing=5.0
        elif rsi<=70 and st<=85:
            timing=2.5
    return round(float(score+timing),3)


def passes(r,cfg):
    def ge(k,v):
        x=r.get(k,np.nan); return bool(np.isfinite(x) and float(x)>=v)
    if not ge('momentum_12w_pct',cfg['m12']): return False
    if 'm26' in cfg and not ge('momentum_26w_pct',cfg['m26']): return False
    if 'accel4' in cfg and not ge('acceleration_4w_pct',cfg['accel4']): return False
    if 'd20' in cfg and not ge('distance_sma20_pct',cfg['d20']): return False
    if 'd50' in cfg and not ge('distance_sma50_pct',cfg['d50']): return False
    if 'rsi_max' in cfg:
        x=r.get('rsi14',np.nan)
        if not np.isfinite(x) or float(x)>cfg['rsi_max']: return False
    if 'stoch_max' in cfg:
        x=r.get('stoch_k',np.nan)
        if not np.isfinite(x) or float(x)>cfg['stoch_max']: return False
    return True


def select_sequential(df,cfg):
    z=df.copy()
    z['signal_date']=pd.to_datetime(z.signal_date)
    z['entry_date']=pd.to_datetime(z.entry_date)
    z['year']=z.signal_date.dt.year
    z['potential_score_v7']=z.apply(potential_score,axis=1)
    z=z[z.apply(lambda r: passes(r,cfg),axis=1)].copy()
    # De-duplicate same symbol/signal date across the two frozen entry models.
    z=z.sort_values(['signal_date','symbol','potential_score_v7'],ascending=[True,True,False])
    z=z.drop_duplicates(['signal_date','symbol'],keep='first')
    selected=[]; counts={}
    for dt,g in z.groupby('signal_date',sort=True):
        year=int(pd.Timestamp(dt).year); used=counts.get(year,0)
        if used>=ANNUAL_CAP: continue
        g=g.sort_values(['potential_score_v7','momentum_12w_pct','momentum_26w_pct'],ascending=False)
        take=min(WEEKLY_CAP,ANNUAL_CAP-used,len(g))
        if take<=0: continue
        pick=g.head(take).copy(); selected.append(pick); counts[year]=used+len(pick)
    if not selected: return z.iloc[0:0].copy()
    return pd.concat(selected,ignore_index=True)


def metrics(df):
    if df.empty:
        return {'trades':0,'win_rate_pct':None,'mean_return_pct':None,'reward_risk':None,'profit_factor':None,'p10_return_pct':None,
                'tp_d_count':0,'tp_d_rate_pct':None,'stop_count':0,'false_positive_count':0,'avg_potential_score':None}
    s=summarize_bucket(df,'selection','x')
    n=len(df); cats=df.exit_category.astype(str)
    return {
      'trades':int(n),'win_rate_pct':s['win_rate_pct'],'mean_return_pct':s['mean_return_pct'],
      'reward_risk':s['reward_risk'],'profit_factor':s['profit_factor'],'p10_return_pct':s['p10_return_pct'],
      'tp_d_count':int((cats=='D_REVERSAL').sum()),'tp_d_rate_pct':round(100*float((cats=='D_REVERSAL').mean()),2),
      'stop_count':int((cats=='PROTECTIVE_STOP').sum()),'false_positive_count':int((cats=='EARLY_FALSE_POSITIVE').sum()),
      'avg_potential_score':round(float(df.potential_score_v7.mean()),3),
    }


def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger)
    df=df[~df.endpoint_mark.astype(bool)].copy()
    if df.empty: raise RuntimeError('no realised PIT-enriched trades')
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any():
        raise RuntimeError('lookahead feature timestamp')

    rows=[]; selected_frames=[]
    years=sorted(pd.to_datetime(df.signal_date).dt.year.unique().tolist())
    for cfg in CONFIGS:
        sel=select_sequential(df,cfg); sel['config']=cfg['name']; selected_frames.append(sel)
        allm=metrics(sel); rows.append({'config':cfg['name'],'period':'ALL',**allm})
        for y in years:
            sy=sel[pd.to_datetime(sel.signal_date).dt.year.eq(y)]
            rows.append({'config':cfg['name'],'period':str(y),**metrics(sy)})
    res=pd.DataFrame(rows)

    # Conservative choice: among configurations respecting <=30 entries in every
    # year, require >=20 total trades; rank by RR, then PF, then mean return. This is
    # still same-bank research and is explicitly not clean OOS.
    eligible=[]
    for cfg in CONFIGS:
        name=cfg['name']; sub=res[res.config.eq(name)]
        yr=sub[sub.period!='ALL']
        allr=sub[sub.period=='ALL'].iloc[0]
        cap_ok=bool((yr.trades<=ANNUAL_CAP).all())
        sample_ok=bool(allr.trades>=20)
        eligible.append({'config':name,'cap_ok':cap_ok,'sample_ok':sample_ok,'rr':allr.reward_risk,'pf':allr.profit_factor,
                         'mean':allr.mean_return_pct,'trades':int(allr.trades)})
    e=pd.DataFrame(eligible)
    ok=e[e.cap_ok & e.sample_ok & e.rr.notna() & e.pf.notna()].copy()
    best=None
    if not ok.empty:
        best=ok.sort_values(['rr','pf','mean'],ascending=False).iloc[0].to_dict()

    errors=[]
    for frame in selected_frames:
        if not frame.empty:
            annual=frame.groupby(pd.to_datetime(frame.signal_date).dt.year).size()
            if (annual>ANNUAL_CAP).any(): errors.append('annual capacity exceeded')
            weekly=frame.groupby(pd.to_datetime(frame.signal_date)).size()
            if (weekly>WEEKLY_CAP).any(): errors.append('weekly capacity exceeded')
    payload={
      'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_SELECTION_CAPACITY_V7_TOP30',
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),'realised_trades_bank':int(len(df)),
      'capacity_rule':{'annual_max_entries':ANNUAL_CAP,'weekly_max_entries':WEEKLY_CAP,'sequential_no_future_year_ranking':True},
      'selection_configs':CONFIGS,'best_same_bank_candidate':best,
      'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,
                            'future_signals_not_used_for_current_ranking':True,'locked_trade_ledger_reused':True,
                            'locked_entry_exit_unchanged':True,'outcome_not_used_in_candidate_score':True},
      'interpretation_rule':'TAKE_PROFIT_OUTCOME_IS_MEASURED_AFTER_SELECTION_AND_NEVER_USED_AS_AN_EX_ANTE_FILTER',
      'results':res.to_dict('records'),'validation_errors':errors,
      'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE',
                     'NO_HISTORICAL_ANALYST_CONSENSUS','TECHNICAL_POTENTIAL_PROXY_ONLY','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    res.to_csv(OUT_CSV,index=False)
    pd.concat(selected_frames,ignore_index=True).to_csv(OUT_TRADES,index=False)
    lines=['# AT Weekly Selection Capacity V7 — Top 30/year','',f"Status: **{payload['status']}**",'',
           'Rule: **maximum 30 executed entries per calendar year**, selected sequentially with completed-week PIT data only; maximum one new selection per signal week. Reaching take-profit is an observed outcome, never a selection input.','',
           '| Config | Period | Trades | Win % | Mean % | RR | PF | P10 % | D take-profit | D TP % | Stops | Early FP | Avg potential score |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in res.to_dict('records'):
        lines.append(f"| {r['config']} | {r['period']} | {r['trades']} | {r['win_rate_pct']} | {r['mean_return_pct']} | {r['reward_risk']} | {r['profit_factor']} | {r['p10_return_pct']} | {r['tp_d_count']} | {r['tp_d_rate_pct']} | {r['stop_count']} | {r['false_positive_count']} | {r['avg_potential_score']} |")
    lines += ['',f"Best same-bank candidate: **{None if best is None else best['config']}**. This is not a clean OOS promotion."]
    OUT_MD.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'best':best,'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__': run()
