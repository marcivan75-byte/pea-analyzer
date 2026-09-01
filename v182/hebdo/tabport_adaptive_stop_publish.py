"""Diagnostic TABPORT des stops adaptatifs a l'ATR, sans modifier le moteur principal.

Les regles sont predeclarees et PIT: l'ATR14 utilise est celui du signal confirme avant
l'entree. Aucune information future ne determine la largeur du stop. Le holdout reste verrouille.
"""
from __future__ import annotations

import argparse, json
from dataclasses import dataclass
from math import floor
from pathlib import Path
import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_publish import read_cache, build_weekly_meta_signals
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import _period_trade_metrics, overall_summary

RULES={
    'FIXED_09': lambda atr: 0.09,
    'ATR2_CAP13': lambda atr: min(0.13,max(0.09,2.0*atr)),
    'ATR2_CAP15': lambda atr: min(0.15,max(0.09,2.0*atr)),
    'ATR2_5_CAP15': lambda atr: min(0.15,max(0.09,2.5*atr)),
}


@dataclass(frozen=True)
class AdaptiveConfig:
    initial_cash:float=65000.0; max_positions:int=12; max_position_eur:float=4500.0
    max_entries_month:int=5; max_entries_year:int=40; fee_rate:float=0.002; slippage_rate:float=0.001
    max_hold_sessions:int=126


class AdaptiveRunner:
    def __init__(self,rule_name:str,cfg:AdaptiveConfig|None=None,risk_parity:bool=False):
        if rule_name not in RULES: raise ValueError('BLOCK_ADAPT_STOP: unknown rule')
        self.rule_name=rule_name; self.rule=RULES[rule_name]; self.cfg=cfg or AdaptiveConfig(); self.risk_parity=bool(risk_parity)
        self.scenario_name=f'{rule_name}_RISK_PARITY' if self.risk_parity else rule_name

    def run(self,signals:pd.DataFrame,prices:pd.DataFrame)->dict:
        s=Tabport65k._normalize_signals(signals); p=Tabport65k._normalize_prices(prices)
        if 'atr_14_pct' not in s.columns: raise ValueError('BLOCK_ADAPT_STOP: atr_14_pct missing')
        s['atr_14_pct']=pd.to_numeric(s['atr_14_pct'],errors='coerce')
        if s['atr_14_pct'].isna().any() or (s['atr_14_pct']<=0).any(): raise ValueError('BLOCK_ADAPT_STOP: invalid PIT ATR')
        if 'tier' in s.columns: s=s[s['tier'].isin(('TCT','CT_WATCH'))].copy()
        s=s[s['EV_net']>=0].copy();
        if s.empty: raise ValueError('BLOCK_ADAPT_STOP: no eligible signals')
        price_dates=p.groupby('ticker')['date'].apply(list).to_dict(); last_price_date=p.groupby('ticker')['date'].max().to_dict()
        scheduled={}; skipped=[]
        for _,row in s.iterrows():
            nxt=next((d for d in price_dates.get(row['ticker'],[]) if d>row['date']),None)
            if nxt is None: skipped.append({'signal_date':row['date'],'ticker':row['ticker'],'reason':'NO_J1_BAR'})
            else: scheduled.setdefault(nxt,[]).append(row.to_dict())
        bars_by_date={d:g.set_index('ticker') for d,g in p.groupby('date',sort=True)}; all_dates=sorted(bars_by_date)
        cash=float(self.cfg.initial_cash); positions={}; ledger=[]; equity=[]; em={}; ey={}
        def close(ticker,date,reason,raw_exit):
            nonlocal cash
            pos=positions.pop(ticker); sell_px=float(raw_exit)*(1-self.cfg.slippage_rate); gross=sell_px*pos['shares']; fee=gross*self.cfg.fee_rate
            cash+=gross-fee; pnl=(gross-fee)-pos['cash_out']
            ledger.append({'ticker':ticker,'signal_date':pos['signal_date'],'entry_date':pos['entry_date'],'exit_date':date,
                'shares':pos['shares'],'entry_price':pos['entry_price'],'exit_price':sell_px,'entry_fee':pos['entry_fee'],'exit_fee':fee,
                'fees_total':pos['entry_fee']+fee,'slippage_rate_side':self.cfg.slippage_rate,'cash_invested':pos['cash_out'],'pnl_net':pnl,
                'return_net':pnl/pos['cash_out'],'exit_reason':reason,'sessions_held':pos['sessions'],'mae':pos['mae'],'mfe':pos['mfe'],
                'EV_net_signal':pos['EV_net'],'atr_14_pct_signal':pos['atr'],'stop_pct_signal':pos['stop_pct'],'stop_rule':self.rule_name,
                'sizing_policy':'CONSTANT_STOP_RISK' if self.risk_parity else 'FIXED_NOTIONAL'})
        def mark(ticker,bar):
            pos=positions[ticker]; pos['last_close']=float(bar['close']); pos['mae']=min(pos['mae'],float(bar['low'])/pos['entry_price']-1); pos['mfe']=max(pos['mfe'],float(bar['high'])/pos['entry_price']-1)
            return pos['entry_price']*(1-pos['stop_pct']),float(bar['open'])
        for date in all_dates:
            day=bars_by_date[date]
            for ticker in list(positions):
                if ticker not in day.index: continue
                bar=day.loc[ticker]; positions[ticker]['sessions']+=1; stop_level,op=mark(ticker,bar)
                if float(bar['low'])<=stop_level:
                    raw=op if op<stop_level else stop_level; close(ticker,date,'STOP_GAP_THROUGH' if raw<stop_level else 'STOP_ADAPT',raw)
                elif positions[ticker]['sessions']>=self.cfg.max_hold_sessions: close(ticker,date,'TIME_26W',float(bar['close']))
                elif date==last_price_date[ticker]: close(ticker,date,'EOP_DATA_END',float(bar['close']))
            candidates=sorted(scheduled.get(date,[]),key=lambda r:(-float(r['EV_net']),str(r['ticker'])))
            for sig in candidates:
                ticker=str(sig['ticker'])
                if ticker in positions: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'ALREADY_OPEN'}); continue
                if ticker not in day.index: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'NO_ENTRY_BAR'}); continue
                if len(positions)>=self.cfg.max_positions: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'MAX_POSITIONS'}); continue
                ym=(date.year,date.month)
                if em.get(ym,0)>=self.cfg.max_entries_month: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'MAX_ENTRIES_MONTH'}); continue
                if ey.get(date.year,0)>=self.cfg.max_entries_year: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'MAX_ENTRIES_YEAR'}); continue
                bar=day.loc[ticker]; buy=float(bar['open'])*(1+self.cfg.slippage_rate)
                atr=float(sig['atr_14_pct']); stop_pct=float(self.rule(atr))
                position_cap=self.cfg.max_position_eur
                if self.risk_parity:
                    # Le budget de perte avant frais reste celui de 4 500 EUR a -9 %.
                    position_cap*=0.09/stop_pct
                affordable=min(position_cap,cash); shares=floor(affordable/(buy*(1+self.cfg.fee_rate)))
                if shares<1: continue
                gross=shares*buy; entry_fee=gross*self.cfg.fee_rate; cash_out=gross+entry_fee
                if cash_out>cash+1e-9: continue
                cash-=cash_out
                positions[ticker]={'signal_date':sig['date'],'entry_date':date,'shares':shares,'entry_price':buy,'entry_fee':entry_fee,'cash_out':cash_out,
                    'EV_net':float(sig['EV_net']),'sessions':1,'mae':0.0,'mfe':0.0,'last_close':float(bar['close']),'atr':atr,'stop_pct':stop_pct}
                em[ym]=em.get(ym,0)+1; ey[date.year]=ey.get(date.year,0)+1
                stop_level,op=mark(ticker,bar)
                if float(bar['low'])<=stop_level:
                    raw=op if op<stop_level else stop_level; close(ticker,date,'STOP_GAP_THROUGH' if raw<stop_level else 'STOP_ADAPT',raw)
                elif date==last_price_date[ticker]: close(ticker,date,'EOP_DATA_END',float(bar['close']))
            mv=sum(pos['shares']*pos['last_close'] for pos in positions.values()); equity.append({'date':date,'cash':cash,'market_value':mv,'equity':cash+mv,'open_positions':len(positions)})
        if positions: raise ValueError(f'BLOCK_ADAPT_STOP: unclosed {sorted(positions)}')
        return {'ledger':pd.DataFrame(ledger),'equity':pd.DataFrame(equity).sort_values('date').reset_index(drop=True),'skipped':pd.DataFrame(skipped)}


def _trade_key(df:pd.DataFrame)->pd.Series:
    return df['ticker'].astype(str)+'|'+pd.to_datetime(df['signal_date'],utc=True).astype(str)


def _attribution(base:pd.DataFrame,candidate:pd.DataFrame)->tuple[pd.DataFrame,dict]:
    """Decompose l'ecart sans confondre effet stop et effet de chemin d'allocation."""
    b=base.copy(); c=candidate.copy(); b['trade_key']=_trade_key(b); c['trade_key']=_trade_key(c)
    cols=['trade_key','ticker','signal_date','entry_date','exit_date','pnl_net','return_net','exit_reason','mae','mfe','stop_pct_signal']
    z=b[cols].merge(c[cols],on='trade_key',how='outer',suffixes=('_fixed','_candidate'),indicator=True)
    z['pnl_net_fixed']=pd.to_numeric(z['pnl_net_fixed'],errors='coerce').fillna(0.0)
    z['pnl_net_candidate']=pd.to_numeric(z['pnl_net_candidate'],errors='coerce').fillna(0.0)
    z['pnl_delta']=z['pnl_net_candidate']-z['pnl_net_fixed']
    matched=z['_merge'].eq('both'); only_c=z['_merge'].eq('right_only'); only_b=z['_merge'].eq('left_only')
    candidate_positive=pd.to_numeric(c['pnl_net'],errors='coerce').clip(lower=0)
    top_idx=pd.to_numeric(c['pnl_net'],errors='coerce').idxmax(); top=c.loc[top_idx]
    top_key=str(top['ticker'])+'|'+str(pd.to_datetime(top['signal_date'],utc=True))
    total_delta=float(c['pnl_net'].sum()-b['pnl_net'].sum())
    top_only=float(z.loc[(z['trade_key']==top_key)&only_c,'pnl_delta'].sum())
    stats={
        'matched_trades':int(matched.sum()),'candidate_only_trades':int(only_c.sum()),'fixed_only_trades':int(only_b.sum()),
        'matched_pnl_delta_eur':float(z.loc[matched,'pnl_delta'].sum()),
        'candidate_only_pnl_eur':float(z.loc[only_c,'pnl_net_candidate'].sum()),
        'fixed_only_pnl_eur':float(z.loc[only_b,'pnl_net_fixed'].sum()),
        'total_pnl_delta_eur':total_delta,'top_candidate_trade_key':top_key,
        'top_candidate_pnl_eur':float(top['pnl_net']),
        'top_candidate_share_positive_pnl_pct':float(100*float(top['pnl_net'])/candidate_positive.sum()) if candidate_positive.sum()>0 else None,
        'delta_excluding_top_candidate_only_trade_eur':float(total_delta-top_only),
        'robust_without_top_candidate_only_trade':bool(total_delta-top_only>0),
    }
    return z.sort_values('pnl_delta',ascending=False).reset_index(drop=True),stats


def _time_stability(ledger:pd.DataFrame,scenario:str)->pd.DataFrame:
    x=ledger.copy(); x['entry_date']=pd.to_datetime(x['entry_date'],utc=True)
    x['period']=x['entry_date'].dt.tz_localize(None).dt.to_period('Y').astype(str)
    rows=[]
    for period,g in x.groupby('period',sort=True):
        row={'scenario':scenario,'period':period}; row.update(_period_trade_metrics(g)); rows.append(row)
    return pd.DataFrame(rows)


def _extreme_bar_audit(ledger:pd.DataFrame,ohlcv:pd.DataFrame)->pd.DataFrame:
    """Expose les barres responsables des MFE extremes; aucune barre n'est reparee."""
    rows=[]
    for _,trade in ledger.nlargest(min(10,len(ledger)),'mfe').iterrows():
        g=ohlcv[(ohlcv['ticker'].astype(str)==str(trade['ticker'])) &
                (ohlcv['date']>=pd.to_datetime(trade['entry_date'],utc=True)) &
                (ohlcv['date']<=pd.to_datetime(trade['exit_date'],utc=True))].sort_values('date').copy()
        if g.empty: continue
        idx=pd.to_numeric(g['high'],errors='coerce').idxmax(); bar=g.loc[idx]; loc=g.index.get_loc(idx)
        prev_close=float(g.iloc[loc-1]['close']) if loc>0 else np.nan
        rows.append({'ticker':trade['ticker'],'signal_date':trade['signal_date'],'entry_date':trade['entry_date'],'exit_date':trade['exit_date'],
            'entry_price':float(trade['entry_price']),'mfe_ledger':float(trade['mfe']),'max_high_date':bar['date'],
            'max_high':float(bar['high']),'bar_open':float(bar['open']),'bar_low':float(bar['low']),'bar_close':float(bar['close']),
            'previous_close':prev_close,'high_vs_prev_close':float(bar['high']/prev_close-1) if np.isfinite(prev_close) else np.nan,
            'close_vs_prev_close':float(bar['close']/prev_close-1) if np.isfinite(prev_close) else np.nan,
            'intraday_high_vs_close':float(bar['high']/bar['close']-1),
            'extreme_data_review_required':bool(float(trade['mfe'])>2 or (np.isfinite(prev_close) and abs(float(bar['close']/prev_close-1))>0.5))})
    return pd.DataFrame(rows)


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); ohlcv,_=read_cache(cache_dir)
    signals,_=build_weekly_meta_signals(ohlcv); feats=add_antifp_features(ohlcv[ohlcv['ticker'].astype(str).isin(set(signals['ticker'].astype(str)))].copy()); confirmed,audit=apply_j1_confirmation(signals,feats)
    if confirmed.empty: raise ValueError('BLOCK_ADAPT_STOP: no confirmed signals')
    confirmed.to_csv(out/'TABPORT_ADAPTIVE_STOP_CONFIRMED.csv',index=False); audit.to_csv(out/'TABPORT_ADAPTIVE_STOP_CONFIRM_AUDIT.csv',index=False)
    plain=ohlcv[['date','ticker','open','high','low','close']].copy(); scenarios=[]; results={}; stability=[]
    specs=[(rule,False) for rule in RULES]+[('ATR2_5_CAP15',True)]
    for rule,risk_parity in specs:
        runner=AdaptiveRunner(rule,risk_parity=risk_parity); scenario=runner.scenario_name
        res=runner.run(confirmed,plain); results[scenario]=res; s=overall_summary(res['ledger'],res['equity'],65000.0); s['scenario']=scenario
        if not res['ledger'].empty:
            s['avg_stop_pct_signal']=float(res['ledger']['stop_pct_signal'].mean()); s['median_stop_pct_signal']=float(res['ledger']['stop_pct_signal'].median()); s['max_stop_pct_signal']=float(res['ledger']['stop_pct_signal'].max())
        scenarios.append(s); stability.append(_time_stability(res['ledger'],scenario)); d=out/scenario.lower(); d.mkdir(parents=True,exist_ok=True); res['ledger'].to_csv(d/'ledger.csv',index=False); res['equity'].to_csv(d/'nav.csv',index=False); res['skipped'].to_csv(d/'skipped.csv',index=False)
    comp=pd.DataFrame(scenarios); comp.to_csv(out/'TABPORT_ADAPTIVE_STOP_COMPARISON.csv',index=False); base=scenarios[0]
    deltas=[]
    for s in scenarios[1:]:
        deltas.append({'scenario':s['scenario'],'return_delta_pct':float(s['rendement_total_depuis_65000_pct']-base['rendement_total_depuis_65000_pct']),
            'win_rate_delta_pct':float(s['taux_gain_pct']-base['taux_gain_pct']),'pf_delta':float(s['profit_factor']-base['profit_factor']),
            'rr_delta':float(s['rr_payoff']-base['rr_payoff']),'expectancy_delta_pct':float(s['esperance_pct']-base['esperance_pct']),
            'stops_delta':int(s['stops']-base['stops']),'drawdown_delta_pct':float(s['drawdown_max_pct']-base['drawdown_max_pct'])})
    pd.concat(stability,ignore_index=True).to_csv(out/'TABPORT_ADAPTIVE_STOP_YEAR_STABILITY.csv',index=False)
    robustness={}
    for scenario in ('ATR2_5_CAP15','ATR2_5_CAP15_RISK_PARITY'):
        table,stats=_attribution(results['FIXED_09']['ledger'],results[scenario]['ledger']); table.to_csv(out/f'TABPORT_{scenario}_ATTRIBUTION.csv',index=False); robustness[scenario]=stats
    extreme=_extreme_bar_audit(results['ATR2_5_CAP15']['ledger'],ohlcv); extreme.to_csv(out/'TABPORT_ADAPTIVE_STOP_EXTREME_BAR_AUDIT.csv',index=False)
    candidate=next(s for s in scenarios if s['scenario']=='ATR2_5_CAP15_RISK_PARITY')
    economic_checks={
        'return_improved':bool(candidate['rendement_total_depuis_65000_pct']>base['rendement_total_depuis_65000_pct']),
        'pf_improved':bool(candidate['profit_factor']>base['profit_factor']),
        'rr_improved':bool(candidate['rr_payoff']>base['rr_payoff']),
        'expectancy_improved':bool(candidate['esperance_pct']>base['esperance_pct']),
        'stops_not_increased':bool(candidate['stops']<=base['stops']),
        'drawdown_not_worse':bool(candidate['drawdown_max_pct']>=base['drawdown_max_pct']),
        'positive_without_top_candidate_only_trade':bool(robustness['ATR2_5_CAP15_RISK_PARITY']['robust_without_top_candidate_only_trade']),
    }
    promoted=bool(all(economic_checks.values()))
    diag={'status':'PUBLISHED','name':'TABPORT_PIT_ATR_ADAPTIVE_STOP_ABLATION','retuning':False,'holdout_unlocked':False,'selection_changed':False,'ranking_changed':False,
          'atr_source':'atr_14_pct from confirmed PIT signal before entry','rules':list(RULES),'confirmed_signals':int(len(confirmed)),'scenarios':scenarios,'deltas_vs_fixed_09':deltas,
          'robustness_attribution':robustness,'extreme_bar_review_required':bool(extreme.get('extreme_data_review_required',pd.Series(dtype=bool)).any()),
          'risk_parity_economic_checks':economic_checks,'promoted':promoted,
          'decision':'PROMOTE_ATR2_5_CAP15_RISK_PARITY' if promoted else 'REJECT_KEEP_FIXED_09',
          'promotion_rule':'Promotion requires all economic checks, resilience after removing the largest candidate-only trade, and no worse drawdown. Original Tabport65k remains unchanged unless a later governed promotion is explicit.'}
    (out/'TABPORT_ADAPTIVE_STOP_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8'); print(json.dumps(diag,default=str)); return diag


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',default='data/cache/actions'); p.add_argument('--output-dir',default='outputs/tabport_adaptive_stop'); a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=='__main__': main()
