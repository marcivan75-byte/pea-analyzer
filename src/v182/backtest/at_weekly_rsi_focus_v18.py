"""Research-only V18: add one existing AT discriminator (RSI14) to V17 A4/breakout ranking.

No new data collection, no exit changes, no continuous optimization. V17 showed A4/breakout
ranking alone did not separate 2024 winners from losers; RSI14 is already available PIT and was
previously identified as a discriminator. Only coarse floors/weights are tested.
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
OUT_JSON=OUTDIR/'AT_WEEKLY_RSI_FOCUS_V18.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_RSI_FOCUS_V18.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_RSI_FOCUS_V18_TRADES.csv'
OUT_EQUITY=OUTDIR/'AT_WEEKLY_RSI_FOCUS_V18_EQUITY.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_RSI_FOCUS_V18.md'
COOLDOWN_WEEKS=4; WEEKLY_CAP=3

CANDIDATES=[
 {'name':'R75_AB','rsi_min':75.0,'floor':'ALL','wa':0.50,'wb':0.50,'wr':0.0},
 {'name':'R80_AB','rsi_min':80.0,'floor':'ALL','wa':0.50,'wb':0.50,'wr':0.0},
 {'name':'R75_A30_OR_B10','rsi_min':75.0,'floor':'A30_OR_B10','wa':0.50,'wb':0.50,'wr':0.0},
 {'name':'ABR_40_40_20','rsi_min':None,'floor':'ALL','wa':0.40,'wb':0.40,'wr':0.20},
 {'name':'R75_ABR_40_40_20','rsi_min':75.0,'floor':'ALL','wa':0.40,'wb':0.40,'wr':0.20},
 {'name':'R80_ABR_40_40_20','rsi_min':80.0,'floor':'ALL','wa':0.40,'wb':0.40,'wr':0.20},
]

def clip01(v,lo,hi):
    v=pd.to_numeric(v,errors='coerce'); return ((v-lo)/(hi-lo)).clip(0,1)

def apply_floor(z,c):
    a=pd.to_numeric(z.acceleration_4w_pct,errors='coerce'); b=pd.to_numeric(z.breakout_above_prior_52w_high_pct,errors='coerce'); r=pd.to_numeric(z.rsi14,errors='coerce')
    m=pd.Series(True,index=z.index)
    if c['floor']=='A30_OR_B10': m &= ((a>=30)|(b>=10))
    if c['rsi_min'] is not None: m &= (r>=c['rsi_min'])
    return z[m].copy()

def select_candidate(df,c):
    z=apply_floor(df,c); z['signal_date']=pd.to_datetime(z.signal_date)
    z['a_norm']=clip01(z.acceleration_4w_pct,-20,100); z['b_norm']=clip01(z.breakout_above_prior_52w_high_pct,-20,80); z['r_norm']=clip01(z.rsi14,60,95)
    z['rank_score_v18']=100*(c['wa']*z.a_norm+c['wb']*z.b_norm+c['wr']*z.r_norm)
    z=z.sort_values(['signal_date','symbol','rank_score_v18'],ascending=[True,True,False]).drop_duplicates(['signal_date','symbol'])
    selected=[]; annual={}; last_sym={}
    for dt,g in z.groupby('signal_date',sort=True):
        y=int(pd.Timestamp(dt).year); used=annual.get(y,0)
        if used>=ANNUAL_CAP: continue
        cand=[]
        for _,row in g.sort_values(['rank_score_v18','rsi14','acceleration_4w_pct'],ascending=False).iterrows():
            prev=last_sym.get(row.symbol)
            if prev is not None and (pd.Timestamp(dt)-prev).days<7*COOLDOWN_WEEKS: continue
            cand.append(row)
        if not cand: continue
        pick=pd.DataFrame(cand).head(min(WEEKLY_CAP,ANNUAL_CAP-used))
        if pick.empty: continue
        selected.append(pick); annual[y]=used+len(pick)
        for _,row in pick.iterrows(): last_sym[row.symbol]=pd.Timestamp(dt)
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
        rr_ok=bool(tm['rr'] is not None and tm['rr']>3.3); pf_ok=bool(tm['pf'] is not None and tm['pf']>=2); dd_ok=bool(ps.get('max_drawdown_pct') is not None and ps['max_drawdown_pct']>=-MAX_DRAWDOWN_PCT)
        annual_net={str(x['year']):x['net_return_pct'] for x in ps.get('annual',[])}
        row={'candidate':c['name'],'rsi_min':c['rsi_min'],'floor':c['floor'],'wa':c['wa'],'wb':c['wb'],'wr':c['wr'],**tm,'annual_entry_counts':counts,'annual_net_return_pct':annual_net,'complete_year_mean_net_pct':ps.get('complete_year_mean_net_pct'),'complete_year_min_net_pct':ps.get('complete_year_min_net_pct'),'max_drawdown_pct':ps.get('max_drawdown_pct'),'max_live_positions_seen':ps.get('max_live_positions_seen'),'cumulative_net_return_pct':ps.get('cumulative_net_return_pct'),'strict_each_complete_year_gt15':strict,'rr_gt_3_3':rr_ok,'pf_ge_2':pf_ok,'drawdown_le_12':dd_ok,'promotion_candidate':bool(strict and rr_ok and pf_ok and dd_ok)}
        rows.append(row); portfolios[c['name']]=ps
        if not audit.empty: audit=audit.copy(); audit['candidate']=c['name']; audits.append(audit)
        if not eq.empty: eq=eq.copy(); eq['candidate']=c['name']; equities.append(eq)
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_RSI_FOCUS_V18','generated_at_utc':datetime.now(timezone.utc).isoformat(),'objective':'TEST_ONE_ADDITIONAL_EXISTING_AT_DISCRIMINATOR_RSI14_WITH_A4_BREAKOUT','portfolio_contract':{'initial_capital_eur':INITIAL_CAPITAL,'full_nominal_per_title_eur':FULL_NOMINAL_EUR,'max_live_positions':MAX_LIVE_POSITIONS,'annual_entry_cap':ANNUAL_CAP,'max_drawdown_pct':MAX_DRAWDOWN_PCT,'weekly_cap':WEEKLY_CAP,'cooldown_weeks':COOLDOWN_WEEKS},'promotion_gates':{'each_complete_year_net_gt_pct':15.0,'rr_gt':3.3,'pf_ge':2.0,'max_drawdown_abs_le_pct':12.0},'features_used':['acceleration_4w_pct','breakout_above_prior_52w_high_pct','rsi14'],'candidates':CANDIDATES,'results':rows,'portfolio_results':portfolios,'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'future_outcome_not_used':True,'coarse_thresholds_weights_only':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'no_new_data_collection':True},'validation_errors':sorted(set(errors)),'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','2026_IS_YTD','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    pd.DataFrame(rows).to_csv(OUT_CSV,index=False); (pd.concat(audits,ignore_index=True) if audits else pd.DataFrame()).to_csv(OUT_TRADES,index=False); (pd.concat(equities,ignore_index=True) if equities else pd.DataFrame()).to_csv(OUT_EQUITY,index=False)
    lines=['# AT Weekly V18 — RSI focus','',f"Status: **{payload['status']}**",'','One additional existing feature only: RSI14.','', '| Candidate | Trades | RR | PF | Mean % | 2024 % | 2025 % | 2026 YTD % | DD % | Promotion |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        a=r['annual_net_return_pct']; lines.append(f"| {r['candidate']} | {r['trades']} | {r['rr']} | {r['pf']} | {r['mean_trade_return_pct']} | {a.get('2024')} | {a.get('2025')} | {a.get('2026')} | {r['max_drawdown_pct']} | {r['promotion_candidate']} |")
    OUT_MD.write_text('\n'.join(lines)+'\n'); print(json.dumps({'status':payload['status'],'results':rows,'validation_errors':payload['validation_errors']},indent=2,ensure_ascii=False)); return payload

if __name__=='__main__': run()
