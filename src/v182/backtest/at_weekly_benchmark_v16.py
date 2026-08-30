"""Research-only V16: truth test versus CAC benchmarks.

Compares the strongest recent stock-picking configurations under the corrected PEA60k
portfolio engine against CAC 40 price (^FCHI) and CAC 40 Gross Return (PX1GR.PA).
No current PIT growth-potential data is fabricated; V15 remains NO_GO_DATA_COVERAGE.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd
import yfinance as yf

from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger
from .at_weekly_growth_potential_pit_v1 import enrich_trades
from .at_weekly_staged_entry_v11 import load_daily
from .at_weekly_capacity_quality_v13 import portfolio_sim, trade_metrics, CANDIDATES as V13_CANDIDATES, select_candidate as select_v13
from .at_weekly_ranked_capacity_v14 import CANDIDATES as V14_CANDIDATES, select_candidate as select_v14
from .at_weekly_selection_portfolio_v10 import CANDIDATES as V10_CANDIDATES, select_candidate as select_v10

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_BENCHMARK_V16.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_BENCHMARK_V16.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_BENCHMARK_V16.md'
INITIAL_CAPITAL=60000.0
COMPLETE_YEARS=(2024,2025)
END='2026-08-22'


def metrics_from_equity(eq):
    e=eq.copy().sort_values('date')
    e['date']=pd.to_datetime(e['date'])
    e=e.drop_duplicates('date').set_index('date')
    nav=e['nav_eur'].astype(float)
    r=nav.pct_change().dropna()
    dd=(nav/nav.cummax()-1)*100
    days=max((nav.index[-1]-nav.index[0]).days,1)
    cagr=(nav.iloc[-1]/nav.iloc[0])**(365.25/days)-1 if nav.iloc[0]>0 else np.nan
    vol=float(r.std()*np.sqrt(252)) if len(r)>1 else np.nan
    sharpe=float(r.mean()/r.std()*np.sqrt(252)) if len(r)>1 and r.std()>0 else np.nan
    downside=r[r<0]
    sortino=float(r.mean()/downside.std()*np.sqrt(252)) if len(downside)>1 and downside.std()>0 else np.nan
    annual=[]
    for y in (2024,2025,2026):
        q=nav[nav.index.year==y]
        if q.empty: continue
        prior=nav[nav.index<q.index.min()]
        s=INITIAL_CAPITAL if prior.empty else float(prior.iloc[-1])
        annual.append({'year':y,'period_type':'FULL_YEAR' if y in COMPLETE_YEARS else 'YTD','return_pct':round((float(q.iloc[-1])/s-1)*100,3)})
    return {'cumulative_return_pct':round((nav.iloc[-1]/INITIAL_CAPITAL-1)*100,3),
            'cagr_pct':round(cagr*100,3),'annualized_vol_pct':None if not np.isfinite(vol) else round(vol*100,3),
            'sharpe_0rf':None if not np.isfinite(sharpe) else round(sharpe,3),
            'sortino_0rf':None if not np.isfinite(sortino) else round(sortino,3),
            'max_drawdown_pct':round(float(dd.min()),3),'annual':annual}


def benchmark_equity(ticker):
    h=yf.download(ticker,start='2024-01-01',end=END,auto_adjust=False,progress=False,threads=False)
    if h is None or h.empty: raise RuntimeError(f'benchmark unavailable {ticker}')
    col='Adj Close' if 'Adj Close' in h.columns else 'Close'
    s=h[col]
    if isinstance(s,pd.DataFrame): s=s.iloc[:,0]
    s=pd.to_numeric(s,errors='coerce').dropna()
    if s.empty: raise RuntimeError(f'benchmark empty {ticker}')
    nav=INITIAL_CAPITAL*(s/s.iloc[0])
    return pd.DataFrame({'date':pd.to_datetime(nav.index).tz_localize(None),'nav_eur':nav.values})


def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): raise RuntimeError('lookahead')
    daily=load_daily(); rows=[]; model_equities={}
    configs=[]
    v10={c['name']:c for c in V10_CANDIDATES}
    configs += [('V12_BREAKOUT30_FULL_J0',select_v10(df,v10['BREAKOUT30']))]
    configs += [('V12_STRENGTH_DUAL_FULL_J0',select_v10(df,v10['STRENGTH_DUAL']))]
    v13={c['name']:c for c in V13_CANDIDATES}
    configs += [('V13_P1_DUAL10_100_W2',select_v13(df,v13['P1_DUAL10_100_W2']))]
    v14={c['name']:c for c in V14_CANDIDATES}
    configs += [('V14_BROAD_QM_CD4_W3',select_v14(df,v14['BROAD_QM_CD4_W3']))]
    for name,s in configs:
        ps,eq,audit=portfolio_sim(s,daily)
        m=metrics_from_equity(eq)
        tm=trade_metrics(s)
        rows.append({'name':name,'type':'MODEL','trades':tm['trades'],'rr':tm['rr'],'pf':tm['pf'],**m})
        model_equities[name]=eq
    for name,ticker,kind in [('CAC40_PRICE','^FCHI','PRICE_INDEX'),('CAC40_GR','PX1GR.PA','GROSS_RETURN_INDEX')]:
        eq=benchmark_equity(ticker); m=metrics_from_equity(eq)
        rows.append({'name':name,'type':'BENCHMARK','ticker':ticker,'benchmark_kind':kind,'trades':None,'rr':None,'pf':None,**m})
    cac=next(r for r in rows if r['name']=='CAC40_GR')
    cac_ann={x['year']:x['return_pct'] for x in cac['annual']}
    for r in rows:
        if r['type']!='MODEL': continue
        ann={x['year']:x['return_pct'] for x in r['annual']}
        r['alpha_vs_cacgr_2024_pct']=round(ann.get(2024,np.nan)-cac_ann.get(2024,np.nan),3)
        r['alpha_vs_cacgr_2025_pct']=round(ann.get(2025,np.nan)-cac_ann.get(2025,np.nan),3)
        r['beats_cacgr_both_complete_years']=bool(r['alpha_vs_cacgr_2024_pct']>0 and r['alpha_vs_cacgr_2025_pct']>0)
        r['beats_cacgr_cagr']=bool(r['cagr_pct']>cac['cagr_pct'])
        r['lower_abs_drawdown_than_cacgr']=bool(abs(r['max_drawdown_pct'])<abs(cac['max_drawdown_pct']))
        r['economic_justification']=bool(r['beats_cacgr_both_complete_years'] and r['beats_cacgr_cagr'] and r['lower_abs_drawdown_than_cacgr'])
    decision='CONTINUE_STOCK_PICKING_RESEARCH' if any(r.get('economic_justification') for r in rows if r['type']=='MODEL') else 'NO_CURRENT_ECONOMIC_JUSTIFICATION_VS_CACGR'
    payload={'status':'SUCCESS','version':'AT_WEEKLY_BENCHMARK_V16','generated_at_utc':datetime.now(timezone.utc).isoformat(),
             'decision':decision,'portfolio_contract':{'initial_capital_eur':60000,'max_live_positions':12,'nominal_per_title_eur':4000,'max_entries_per_year':30},
             'benchmark_sources':{'CAC40_PRICE':'Yahoo Finance ^FCHI','CAC40_GR':'Yahoo Finance PX1GR.PA'},
             'results':rows,'pit_growth_gate_status':'V15_NO_GO_DATA_COVERAGE_NOT_BYPASSED',
             'limitations':['MODEL_SAME_BANK_NOT_CLEAN_OOS','CURRENT_UNIVERSE_NOT_PIT_MEMBERSHIP','BENCHMARK_DATA_EXTERNAL_YAHOO','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    flat=[]
    for r in rows:
        x={k:v for k,v in r.items() if k!='annual'}; x['annual_json']=json.dumps(r['annual'],ensure_ascii=False); flat.append(x)
    pd.DataFrame(flat).to_csv(OUT_CSV,index=False)
    lines=['# AT Weekly V16 — Stock picking vs CAC','',f"Status: **{payload['status']}**",f"Decision: **{decision}**",'',
           '| Name | Type | Trades | RR | PF | CAGR % | Cum % | Max DD % | Sharpe | 2024 % | 2025 % | 2026 YTD % | Econ justification |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        a={x['year']:x['return_pct'] for x in r['annual']}
        lines.append(f"| {r['name']} | {r['type']} | {r.get('trades')} | {r.get('rr')} | {r.get('pf')} | {r['cagr_pct']} | {r['cumulative_return_pct']} | {r['max_drawdown_pct']} | {r['sharpe_0rf']} | {a.get(2024)} | {a.get(2025)} | {a.get(2026)} | {r.get('economic_justification')} |")
    lines += ['','CAC40_GR is the primary benchmark because it includes reinvested gross dividends. V15 >20% growth-potential gate remains untested historically due zero genuine PIT coverage.']
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':'SUCCESS','decision':decision,'results':rows},indent=2,ensure_ascii=False))
    return payload

if __name__=='__main__': run()
