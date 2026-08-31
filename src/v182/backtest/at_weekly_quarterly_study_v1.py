"""Research-only quarterly validation study for the locked AT Weekly architecture.

Uses locked entry models, standing 9% stop, early false-positive invalidation and
D-01 confirmed-reversal exits. Block E is retained as diagnostic/context only and
therefore cannot generate an execution in this study. Block F trailing is rejected.
All strategic signals use completed-week information and next-week-open execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json, math
import numpy as np
import pandas as pd

from .at_weekly_v1 import _to_weekly
from .at_weekly_v1_fixed import _cache_files, _iter_consolidated, CACHE_DIRS, MIN_WEEKLY_BARS
from .at_weekly_weight_bank_v1 import build_bars
from .at_weekly_weight_bank_v3_continuous import entry_mask
from .at_weekly_exit_bench_v1 import ENTRIES
from .at_weekly_exit_opt_v8_profit_reversal_blocks import add_indicators, last_daily_change_by_week, simulate

ROOT = Path(__file__).resolve().parents[3]
OUTDIR = ROOT / 'outputs/backtest'
OUT_JSON = OUTDIR / 'AT_WEEKLY_QUARTERLY_STUDY_V1.json'
OUT_SUMMARY = OUTDIR / 'AT_WEEKLY_QUARTERLY_STUDY_V1.csv'
OUT_AUDIT = OUTDIR / 'AT_WEEKLY_QUARTERLY_STUDY_V1_TRADES.csv'
OUT_MD = OUTDIR / 'AT_WEEKLY_QUARTERLY_STUDY_V1.md'
START = pd.Timestamp('2023-01-01')
NOMINAL_EUR = 5000.0
STOP_PCT = 9.0
D_CFG = {
    'family_name':'FULL',
    'family':['daily_drop','bb_reentry','adx_decay','rsi_rev','stoch_rev','psar_break','sma20_break','sma50_break'],
    'activation_pct':5.0,
    'score':1,
    'daily_drop_pct':4.0,
}


def qlabel(ts):
    return pd.Timestamp(ts).to_period('Q').strftime('%YQ%q')


def safe_rr(win_mean, loss_mean):
    if win_mean is None or loss_mean is None or not np.isfinite(win_mean) or not np.isfinite(loss_mean) or loss_mean == 0:
        return None
    return float(win_mean / abs(loss_mean))


def build_universe():
    bars={}; arr={}; sigs={}; first=[]; last=[]
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for sym,hist,err in _iter_consolidated(path):
            if sym is None or err or hist is None or hist.empty:
                continue
            w=_to_weekly(hist)
            if len(w)<MIN_WEEKLY_BARS:
                continue
            b=add_indicators(build_bars(w)); daily=last_daily_change_by_week(hist,b.index)
            bars[sym]=b; first.append(b.index.min()); last.append(b.index.max())
            arr[sym]={
                'open':b.open.to_numpy(float),'low':b.low.to_numpy(float),'close':b.close.to_numpy(float),
                'rsi':b.rsi14.to_numpy(float),'k':b.stoch_k.to_numpy(float),'d':b.stoch_d.to_numpy(float),
                'psar':b.psar.to_numpy(float),'sma20':b.sma20.to_numpy(float),'sma50':b.sma50.to_numpy(float),
                'bb':b.bb_upper.to_numpy(float),'adx':b.adx.to_numpy(float),'pdi':b.plus_di.to_numpy(float),'daily':daily,
            }
            sigs[sym]={e['label']:entry_mask(b,e['weights'],e['threshold_ratio']).to_numpy(bool) for e in ENTRIES}
    return bars,arr,sigs,min(first),max(last)


def potential_signal_rows(bars,sigs):
    rows=[]
    for sym,b in bars.items():
        for e in ENTRIES:
            sig=sigs[sym][e['label']]
            idx=np.flatnonzero(sig)
            for i in idx:
                if i>=len(b)-1:
                    continue
                dt=pd.Timestamp(b.index[i])
                if dt>=START:
                    rows.append({'symbol':sym,'entry_model':e['label'],'signal_date':dt.date().isoformat(),'signal_quarter':qlabel(dt)})
    return pd.DataFrame(rows)


def trade_ledger(bars,arr,sigs):
    rows=[]
    for e in ENTRIES:
        for sym,b in bars.items():
            tr=simulate(sym,b,arr[sym],sigs[sym][e['label']],e['label'],D_CFG,'QUARTERLY_LOCKED_D01')
            for r in tr:
                ed=pd.Timestamp(r['entry_date']); xd=pd.Timestamp(r['exit_date'])
                loc=b.index.get_indexer([ed],method=None)[0]
                signal_date = b.index[loc-1] if loc>0 else pd.NaT
                rr=dict(r)
                rr['signal_date']=None if pd.isna(signal_date) else pd.Timestamp(signal_date).date().isoformat()
                rr['entry_quarter']=qlabel(ed); rr['exit_quarter']=qlabel(xd)
                rr['nominal_eur']=NOMINAL_EUR
                rr['eur_pnl']=0.0 if rr['endpoint_mark'] else NOMINAL_EUR*float(rr['return_pct'])/100.0
                reason=rr['exit_reasons']
                if rr['endpoint_mark']:
                    cat='ENDPOINT_MARK'
                elif 'PROTECTIVE_STOP' in reason:
                    cat='PROTECTIVE_STOP'
                elif 'EARLY_FALSE_POSITIVE' in reason:
                    cat='EARLY_FALSE_POSITIVE'
                elif 'PROFIT_REVERSAL' in reason:
                    cat='D_REVERSAL'
                else:
                    cat='OTHER_INVALIDATION'
                rr['exit_category']=cat
                rr['block_e_overheat_execution']=False
                rows.append(rr)
    return pd.DataFrame(rows)


def aggregate_quarter(q, signals, ledger):
    ent=ledger[(pd.to_datetime(ledger.entry_date)>=START) & (ledger.entry_quarter==q)]
    ex=ledger[(pd.to_datetime(ledger.exit_date)>=START) & (ledger.exit_quarter==q)]
    realised=ex[~ex.endpoint_mark.astype(bool)].copy()
    wins=realised[realised.return_pct>0]; losses=realised[realised.return_pct<0]; zeros=realised[realised.return_pct==0]
    gross=float(realised.eur_pnl.sum()) if len(realised) else 0.0
    deployed=NOMINAL_EUR*len(ent)
    return {
        'quarter':q,
        'potential_entry_signals':int((signals.signal_quarter==q).sum()) if len(signals) else 0,
        'executed_entries':int(len(ent)),
        'realised_closed_exits':int(len(realised)),
        'early_false_positives_eliminated':int((realised.exit_category=='EARLY_FALSE_POSITIVE').sum()),
        'protective_stop_invalidations':int((realised.exit_category=='PROTECTIVE_STOP').sum()),
        'other_invalidations':int((realised.exit_category=='OTHER_INVALIDATION').sum()),
        'take_profit_reversal_D':int((realised.exit_category=='D_REVERSAL').sum()),
        'take_profit_overheat_E':0,
        'take_profit_D_plus_E_joint':0,
        'winning_trades':int(len(wins)),
        'avg_win_pct':None if wins.empty else round(float(wins.return_pct.mean()),3),
        'max_win_pct':None if wins.empty else round(float(wins.return_pct.max()),3),
        'losing_trades':int(len(losses)),
        'avg_loss_pct':None if losses.empty else round(float(losses.return_pct.mean()),3),
        'max_loss_pct':None if losses.empty else round(float(losses.return_pct.min()),3),
        'zero_return_trades':int(len(zeros)),
        'reward_risk':None if wins.empty or losses.empty else round(safe_rr(float(wins.return_pct.mean()),float(losses.return_pct.mean())),3),
        'gross_eur_pnl':round(gross,2),
        'net_before_costs_eur_pnl':round(gross,2),
        'capital_deployed_nominal_eur':round(deployed,2),
        'return_on_deployed_nominal_pct':None if deployed<=0 else round(gross/deployed*100,3),
        'endpoint_marks':int(ex.endpoint_mark.astype(bool).sum()),
        'sample_warning':bool(len(realised)<30),
    }


def consolidated(signals,ledger):
    ent=ledger[pd.to_datetime(ledger.entry_date)>=START]
    ex=ledger[pd.to_datetime(ledger.exit_date)>=START]
    realised=ex[~ex.endpoint_mark.astype(bool)].copy()
    wins=realised[realised.return_pct>0]; losses=realised[realised.return_pct<0]; zeros=realised[realised.return_pct==0]
    gross=float(realised.eur_pnl.sum()) if len(realised) else 0.0
    pf=None
    if len(losses) and -losses.return_pct.sum()>0:
        pf=float(wins.return_pct.sum()/(-losses.return_pct.sum()))
    return {
        'period':'SINCE_2023_01_01',
        'potential_entry_signals':int(len(signals)),
        'executed_entries':int(len(ent)),
        'realised_closed_exits':int(len(realised)),
        'early_false_positives_eliminated':int((realised.exit_category=='EARLY_FALSE_POSITIVE').sum()),
        'protective_stop_invalidations':int((realised.exit_category=='PROTECTIVE_STOP').sum()),
        'other_invalidations':int((realised.exit_category=='OTHER_INVALIDATION').sum()),
        'take_profit_reversal_D':int((realised.exit_category=='D_REVERSAL').sum()),
        'take_profit_overheat_E':0,
        'take_profit_D_plus_E_joint':0,
        'winning_trades':int(len(wins)),
        'win_rate_pct':None if realised.empty else round(float((realised.return_pct>0).mean()*100),2),
        'avg_win_pct':None if wins.empty else round(float(wins.return_pct.mean()),3),
        'max_win_pct':None if wins.empty else round(float(wins.return_pct.max()),3),
        'losing_trades':int(len(losses)),
        'avg_loss_pct':None if losses.empty else round(float(losses.return_pct.mean()),3),
        'max_loss_pct':None if losses.empty else round(float(losses.return_pct.min()),3),
        'zero_return_trades':int(len(zeros)),
        'reward_risk':None if wins.empty or losses.empty else round(safe_rr(float(wins.return_pct.mean()),float(losses.return_pct.mean())),3),
        'profit_factor':None if pf is None else round(pf,3),
        'p10_return_pct':None if realised.empty else round(float(realised.return_pct.quantile(.10)),3),
        'gross_eur_pnl':round(gross,2),
        'net_before_costs_eur_pnl':round(gross,2),
        'capital_deployed_nominal_eur':round(NOMINAL_EUR*len(ent),2),
        'return_on_deployed_nominal_pct':None if len(ent)==0 else round(gross/(NOMINAL_EUR*len(ent))*100,3),
        'endpoint_marks':int(ex.endpoint_mark.astype(bool).sum()),
    }


def validate(quarters,cons,ledger):
    errors=[]
    for r in quarters:
        cats=r['early_false_positives_eliminated']+r['protective_stop_invalidations']+r['other_invalidations']+r['take_profit_reversal_D']+r['take_profit_overheat_E']+r['take_profit_D_plus_E_joint']
        if cats!=r['realised_closed_exits']:
            errors.append(f"exit category reconciliation failed {r['quarter']}: {cats}!={r['realised_closed_exits']}")
        wlz=r['winning_trades']+r['losing_trades']+r['zero_return_trades']
        if wlz!=r['realised_closed_exits']:
            errors.append(f"win/loss reconciliation failed {r['quarter']}: {wlz}!={r['realised_closed_exits']}")
    real=ledger[(pd.to_datetime(ledger.exit_date)>=START)&(~ledger.endpoint_mark.astype(bool))]
    expected=float((real.return_pct*NOMINAL_EUR/100).sum()) if len(real) else 0.0
    if not math.isclose(expected,cons['gross_eur_pnl'],abs_tol=.011):
        errors.append('EUR P&L reconciliation failed')
    return errors


def run():
    bars,arr,sigs,first,last=build_universe()
    signals=potential_signal_rows(bars,sigs)
    ledger=trade_ledger(bars,arr,sigs)
    if ledger.empty:
        raise RuntimeError('no trades generated')
    quarter_set=sorted(set(signals.signal_quarter.tolist()) | set(ledger.loc[pd.to_datetime(ledger.entry_date)>=START,'entry_quarter']) | set(ledger.loc[pd.to_datetime(ledger.exit_date)>=START,'exit_quarter']))
    quarters=[aggregate_quarter(q,signals,ledger) for q in quarter_set if q>='2023Q1']
    cons=consolidated(signals,ledger)
    errors=validate(quarters,cons,ledger)
    payload={
        'status':'SUCCESS' if not errors else 'VALIDATION_FAILED',
        'version':'AT_WEEKLY_QUARTERLY_STUDY_V1',
        'generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'data_window':{'first_week':pd.Timestamp(first).date().isoformat(),'last_week':pd.Timestamp(last).date().isoformat()},
        'locked':{
            'entry_models':[e['label'] for e in ENTRIES],
            'protective_stop_pct':STOP_PCT,
            'false_positive_block':'LOCKED',
            'D01':D_CFG,
            'block_e':'LOCKED_DIAGNOSTIC_NOT_EXECUTION',
            'trailing':'LOCKED_REJECTED',
            'strategic_signals':'completed_week_only',
            'strategic_execution':'next_week_open',
            'endpoint_mark_is_execution':False,
        },
        'nominal_per_trade_eur':NOMINAL_EUR,
        'cost_model':'NONE_VALIDATED_NET_BEFORE_COSTS_EQUALS_GROSS',
        'quarters':quarters,
        'consolidated':cons,
        'validation_errors':errors,
        'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_VALIDATED_FEES_SLIPPAGE','RESEARCH_ONLY'],
    }
    OUTDIR.mkdir(parents=True,exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    pd.DataFrame(quarters).to_csv(OUT_SUMMARY,index=False)
    ledger.to_csv(OUT_AUDIT,index=False)
    lines=['# AT Weekly Quarterly Study V1','',f"Status: **{payload['status']}**",f"Data: {payload['data_window']['first_week']} to {payload['data_window']['last_week']}",'',
           '| Quarter | Potential | Entries | FP early | Stop | D reversal | E overheat | Wins | Avg win % | Max win % | Losses | Avg loss % | Max loss % | RR | P&L EUR |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in quarters:
        lines.append(f"| {r['quarter']} | {r['potential_entry_signals']} | {r['executed_entries']} | {r['early_false_positives_eliminated']} | {r['protective_stop_invalidations']} | {r['take_profit_reversal_D']} | {r['take_profit_overheat_E']} | {r['winning_trades']} | {r['avg_win_pct']} | {r['max_win_pct']} | {r['losing_trades']} | {r['avg_loss_pct']} | {r['max_loss_pct']} | {r['reward_risk']} | {r['gross_eur_pnl']} |")
    lines += ['', '## Consolidated since 2023-01-01', '', '```json', json.dumps(cons,indent=2,ensure_ascii=False), '```', '', 'Costs/slippage are not validated; reported net-before-costs equals gross and is not a real-world net result.']
    OUT_MD.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'quarters':len(quarters),'consolidated':cons,'validation_errors':errors},indent=2,ensure_ascii=False))
    return payload

if __name__=='__main__':
    run()
