"""Research-only V17: focused two-factor AT ranking using only existing PIT features.

V16 showed hard AND gates improve RR/PF but under-utilize capital. V17 keeps exactly the
same two features (4-week acceleration and 52-week breakout) and replaces hard thresholds
with a deterministic ranking plus very broad floors. No new data, no exit changes.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd

from .at_weekly_growth_potential_pit_v1 import enrich_trades
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger
from .at_weekly_capacity_quality_v13 import portfolio_sim, trade_metrics, ANNUAL_CAP, MAX_LIVE_POSITIONS, FULL_NOMINAL_EUR, INITIAL_CAPITAL, MAX_DRAWDOWN_PCT, COMPLETE_YEARS
from .at_weekly_staged_entry_v11 import load_daily

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_RANKED_FOCUS_V17.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_RANKED_FOCUS_V17.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_RANKED_FOCUS_V17_TRADES.csv'
OUT_EQUITY=OUTDIR/'AT_WEEKLY_RANKED_FOCUS_V17_EQUITY.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_RANKED_FOCUS_V17.md'
COOLDOWN_WEEKS=4
WEEKLY_CAP=3

CANDIDATES=[
 {'name':'ALL_50A_50B','floor':'ALL','wa':0.50,'wb':0.50},
 {'name':'ALL_60A_40B','floor':'ALL','wa':0.60,'wb':0.40},
 {'name':'ALL_40A_60B','floor':'ALL','wa':0.40,'wb':0.60},
 {'name':'FLOOR_A20_OR_B0_50_50','floor':'A20_OR_B0','wa':0.50,'wb':0.50},
 {'name':'FLOOR_A20_OR_B0_60_40','floor':'A20_OR_B0','wa':0.60,'wb':0.40},
 {'name':'FLOOR_A20_OR_B0_40_60','floor':'A20_OR_B0','wa':0.40,'wb':0.60},
 {'name':'FLOOR_A20_AND_B0_50_50','floor':'A20_AND_B0','wa':0.50,'wb':0.50},
 {'name':'FLOOR_A30_OR_B10_50_50','floor':'A30_OR_B10','wa':0.50,'wb':0.50},
]

def clip01(v,lo,hi):
    v=pd.to_numeric(v,errors='coerce')
    return ((v-lo)/(hi-lo)).clip(0,1)

def apply_floor(z,kind):
    a=pd.to_numeric(z.acceleration_4w_pct,errors='coerce'); b=pd.to_numeric(z.breakout_above_prior_52w_high_pct,errors='coerce')
    if kind=='ALL': return z.copy()
    if kind=='A20_OR_B0': return z[(a>=20)|(b>=0)].copy()
    if kind=='A20_AND_B0': return z[(a>=20)&(b>=0)].copy()
    if kind=='A30_OR_B10': return z[(a>=30)|(b>=10)].copy()
    raise ValueError(kind)

def select_candidate(df,c):
    z=apply_floor(df,c['floor']); z['signal_date']=pd.to_datetime(z.signal_date)
    z['a_norm']=clip01(z.acceleration_4w_pct,-20,100)
    z['b_norm']=clip01(z.breakout_above_prior_52w_high_pct,-20,80)
    z['rank_score_v17']=100*(c['wa']*z.a_norm+c['wb']*z.b_norm)
    z=z.sort_values(['signal_date','symbol','rank_score_v17'],ascending=[True,True,False]).drop_duplicates(['signal_date','symbol'])
    selected=[]; annual={}; last_sym={}
    for dt,g in z.groupby('signal_date',sort=True):
        y=int(pd.Timestamp(dt).year); used=annual.get(y,0)
        if used>=ANNUAL_CAP: continue
        cand=[]
        for _,r in g.sort_values(['rank_score_v17','acceleration_4w_pct','breakout_above_prior_52w_high_pct'],ascending=False).iterrows():
            prev=last_sym.get(r.symbol)
            if prev is not None and (pd.Timestamp(dt)-prev).days<7*COOLDOWN_WEEKS: continue
            cand.append(r)
        if not cand: continue
        pick=pd.DataFrame(cand).head(min(WEEKLY_CAP,ANNUAL_CAP-used))
        if pick.empty: continue
        selected.append(pick); annual[y]=used+len(pick)
        for _,r in pick.iterrows(): last_sym[r.symbol]=pd.Timestamp(dt)
    return pd.concat(selected,ignore_index=True) if selected else z.iloc[0:0].copy()

def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    daily=load_daily(); rows=[]; portfolios={}; audits=[]; equities=[]
    for c in CANDIDATES:
        s=select_candidate(df,c); tm=trade_metrics(s); ps,eq,audit=portfolio_sim(s,daily)
        counts={int(y):int(n) for y,n in s.groupby(pd.to_datetime(s.signal_date).dt.year).size().items()} if not s.empty else {}
        full=[x for x in ps.get('annual',[]) if x.get('period_type')=='FULL_YEAR']
        strict=bool(len(full)==len(COMPLETE_YEARS) and all(x['net_return_pct']>15 for x in full))
        rr_ok=bool(tm['rr'] is not None and tm['rr']>3.3); pf_ok=bool(tm['pf'] is not None and tm['pf']>=2)
        dd_ok=bool(ps.get('max_drawdown_pct') is not None and ps['max_drawdown_pct']>=-MAX_DRAWDOWN_PCT)
        annual_net={str(x['year']):x['net_return_pct'] for x in ps.get('annual',[])}
        row={'candidate':c['name'],'floor':c['floor'],'wa':c['wa'],'wb':c['wb'],**tm,'annual_entry_counts':counts,'annual_net_return_pct':annual_net,'complete_year_mean_net_pct':ps.get('complete_year_mean_net_pct'),'complete_year_min_net_pct':ps.get('complete_year_min_net_pct'),'max_drawdown_pct':ps.get('max_drawdown_pct'),'max_live_positions_seen':ps.get('max_live_positions_seen'),'cumulative_net_return_pct':ps.get('cumulative_net_return_pct'),'strict_each_complete_year_gt15':strict,'rr_gt_3_3':rr_ok,'pf_ge_2':pf_ok,'drawdown_le_12':dd_ok,'promotion_candidate':bool(strict and rr_ok and pf_ok and dd_ok)}
        rows.append(row); portfolios[c['name']]=ps
        if not audit.empty: audit=audit.copy(); audit['candidate']=c['name']; audits.append(audit)
        if not eq.empty: eq=eq.copy(); eq['candidate']=c['name']; equities.append(eq)
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_RANKED_FOCUS_V17','generated_at_utc':datetime.now(timezone.utc).isoformat(),'objective':'USE_ONLY_A4_AND_BREAKOUT_AS_FOCUSED_RANKING_TO_RECOVER_CAPITAL_UTILIZATION','portfolio_contract':{'initial_capital_eur':INITIAL_CAPITAL,'full_nominal_per_title_eur':FULL_NOMINAL_EUR,'max_live_positions':MAX_LIVE_POSITIONS,'annual_entry_cap':ANNUAL_CAP,'max_drawdown_pct':MAX_DRAWDOWN_PCT,'weekly_cap':WEEKLY_CAP,'cooldown_weeks':COOLDOWN_WEEKS},'promotion_gates':{'each_complete_year_net_gt_pct':15.0,'rr_gt':3.3,'pf_ge':2.0,'max_drawdown_abs_le_pct':12.0},'features_used':['acceleration_4w_pct','breakout_above_prior_52w_high_pct'],'candidates':CANDIDATES,'results':rows,'portfolio_results':portfolios,'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'future_outcome_not_used':True,'fixed_coarse_weights_only':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'no_new_data_collection':True},'validation_errors':sorted(set(errors)),'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','2026_IS_YTD','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    pd.DataFrame(rows).to_csv(OUT_CSV,index=False)
    (pd.concat(audits,ignore_index=True) if audits else pd.DataFrame()).to_csv(OUT_TRADES,index=False)
    (pd.concat(equities,ignore_index=True) if equities else pd.DataFrame()).to_csv(OUT_EQUITY,index=False)
    lines=['# AT Weekly V17 — Focused two-factor ranking','',f"Status: **{payload['status']}**",'','Only A4 acceleration and 52-week breakout are used.','', '| Candidate | Trades | RR | PF | Mean % | 2024 % | 2025 % | 2026 YTD % | DD % | Promotion |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        a=r['annual_net_return_pct']; lines.append(f"| {r['candidate']} | {r['trades']} | {r['rr']} | {r['pf']} | {r['mean_trade_return_pct']} | {a.get('2024')} | {a.get('2025')} | {a.get('2026')} | {r['max_drawdown_pct']} | {r['promotion_candidate']} |")
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'results':rows,'validation_errors':payload['validation_errors']},indent=2,ensure_ascii=False))
    return payload

if __name__=='__main__': run()
