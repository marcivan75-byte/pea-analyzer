"""Research-only V15: hard growth-potential preselection gate.

User rule: a title is not eligible for final preselection unless its ex-ante growth
potential is strictly above +20% at the signal date.

Important methodology rule: current analyst targets are NEVER backfilled into history.
Historical enforcement is allowed only when a genuine point-in-time (PIT) potential
measure, timestamped <= signal date, is available. In its absence, V15 returns a
NO_GO_DATA_COVERAGE decision rather than fabricating a historical potential.

Accepted future/live sources, in priority order:
1. dated analyst consensus target available <= signal date;
2. dated analyst estimate/revision data sufficient to build a PIT fundamental potential;
3. a separately validated PIT fundamental model.
Pure technical momentum scores are diagnostics and cannot satisfy the >20% growth gate.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger
from .at_weekly_growth_potential_pit_v1 import enrich_trades, summarize_bucket

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_GATE_V15.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_GATE_V15.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_GATE_V15.md'
MIN_GROWTH_POTENTIAL_PCT=20.0
MIN_HISTORICAL_COVERAGE_PCT=80.0

PIT_CANDIDATE_COLUMNS=[
    'consensus_upside_pit_pct',
    'growth_potential_pct_pit',
    'fundamental_growth_potential_pct_pit',
]


def choose_pit_potential(df):
    """Return value/source/timestamp columns without inventing unavailable history."""
    out=pd.DataFrame(index=df.index)
    out['growth_potential_pct_pit']=np.nan
    out['growth_potential_source_pit']=None
    out['growth_potential_timestamp']=None
    for col in PIT_CANDIDATE_COLUMNS:
        if col not in df.columns:
            continue
        vals=pd.to_numeric(df[col],errors='coerce')
        take=out['growth_potential_pct_pit'].isna() & vals.notna()
        out.loc[take,'growth_potential_pct_pit']=vals[take]
        out.loc[take,'growth_potential_source_pit']=col
        # Existing consensus_upside_pit_pct in V1 has no independent timestamp column;
        # feature_timestamp is the latest admissible signal-date feature timestamp.
        if 'feature_timestamp' in df.columns:
            out.loc[take,'growth_potential_timestamp']=df.loc[take,'feature_timestamp'].astype(str)
    return out


def run():
    bars,arr,sigs,first,last=build_universe()
    ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger)
    df=df[~df.endpoint_mark.astype(bool)].copy()
    pit=choose_pit_potential(df)
    for c in pit.columns:
        df[c]=pit[c]

    covered=df.growth_potential_pct_pit.notna()
    coverage=100.0*float(covered.mean()) if len(df) else 0.0
    timestamp_ok=True
    if covered.any():
        ts=pd.to_datetime(df.loc[covered,'growth_potential_timestamp'],errors='coerce')
        sig=pd.to_datetime(df.loc[covered,'signal_date'],errors='coerce')
        timestamp_ok=bool(ts.notna().all() and (ts<=sig).all())

    eligible=covered & (df.growth_potential_pct_pit>MIN_GROWTH_POTENTIAL_PCT)
    historical_gate_ready=bool(coverage>=MIN_HISTORICAL_COVERAGE_PCT and timestamp_ok)
    historical_decision='GO_BACKTEST_GATE' if historical_gate_ready else 'NO_GO_DATA_COVERAGE'

    bucket_rows=[]
    if covered.any():
        specs=[(-np.inf,20,'<=20'),(20,30,'20-30'),(30,40,'30-40'),(40,np.inf,'>40')]
        x=df.growth_potential_pct_pit
        for lo,hi,name in specs:
            if name=='<=20': m=x.notna()&(x<=20)
            else: m=x.notna()&(x>lo)&(x<=hi if np.isfinite(hi) else True)
            bucket_rows.append(summarize_bucket(df[m],'growth_potential_pct_pit',name))
    buckets=pd.DataFrame(bucket_rows)

    errors=[]
    if covered.any() and not timestamp_ok: errors.append('PIT potential timestamp after signal or missing')
    payload={
        'status':'SUCCESS' if not errors else 'VALIDATION_FAILED',
        'version':'AT_WEEKLY_GROWTH_POTENTIAL_GATE_V15',
        'generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'preselection_rule':{
            'growth_potential_pct_strictly_gt':MIN_GROWTH_POTENTIAL_PCT,
            'below_or_equal_20_rejected':True,
            'technical_proxy_cannot_satisfy_gate':True,
            'current_consensus_backfill_forbidden':True,
        },
        'historical_data_audit':{
            'realised_trades':int(len(df)),
            'pit_growth_potential_covered_trades':int(covered.sum()),
            'pit_growth_potential_coverage_pct':round(coverage,3),
            'minimum_coverage_pct_for_historical_gate_backtest':MIN_HISTORICAL_COVERAGE_PCT,
            'timestamps_valid_le_signal':timestamp_ok,
            'historical_gate_ready':historical_gate_ready,
            'decision':historical_decision,
            'eligible_gt20_trades_if_available':int(eligible.sum()),
        },
        'source_priority':['DATED_ANALYST_CONSENSUS_TARGET','DATED_ANALYST_ESTIMATES_AND_REVISIONS','VALIDATED_PIT_FUNDAMENTAL_MODEL'],
        'diagnostic_buckets':bucket_rows,
        'lookahead_controls':{
            'feature_timestamp_le_signal_required':True,
            'current_consensus_backfill_forbidden':True,
            'future_trade_outcome_not_used':True,
            'locked_trade_ledger_reused':True,
            'technical_growth_score_not_treated_as_growth_upside':True,
        },
        'next_action':'BUILD_OR_CONNECT_GENUINE_PIT_GROWTH_POTENTIAL_SOURCE' if not historical_gate_ready else 'BACKTEST_STRICT_GT20_GATE',
        'validation_errors':errors,
        'limitations':['NO_FABRICATED_HISTORICAL_CONSENSUS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','RESEARCH_ONLY'],
    }
    OUTDIR.mkdir(parents=True,exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    cols=[c for c in ['symbol','signal_date','entry_date','exit_date','return_pct','growth_potential_pct_pit','growth_potential_source_pit','growth_potential_timestamp'] if c in df.columns]
    df[cols].to_csv(OUT_CSV,index=False)
    lines=['# AT Weekly V15 — Growth potential >20% preselection gate','',f"Status: **{payload['status']}**",'',
           f"Mandatory preselection: **PIT growth potential > {MIN_GROWTH_POTENTIAL_PCT:.0f}%**.",
           'Current analyst targets are forbidden for historical backfill. Technical momentum proxies do not satisfy this gate.','',
           f"Historical PIT coverage: **{coverage:.3f}%**; decision: **{historical_decision}**.",
           '', 'Next action: **'+payload['next_action']+'**.']
    if not buckets.empty:
        lines += ['','| Potential bucket | Trades | Win % | Mean % | RR | PF |','|---|---:|---:|---:|---:|---:|']
        for r in bucket_rows:
            lines.append(f"| {r['bucket']} | {r['trades']} | {r['win_rate_pct']} | {r['mean_return_pct']} | {r['reward_risk']} | {r['profit_factor']} |")
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'audit':payload['historical_data_audit'],'next_action':payload['next_action'],'validation_errors':errors},indent=2,ensure_ascii=False))
    return payload

if __name__=='__main__': run()
