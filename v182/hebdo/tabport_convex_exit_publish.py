"""Diagnostic PIT de prolongation selective des grands gagnants TABPORT.

La regle est predeclaree: sortie normale a 126 seances, sauf si le rendement de
cloture est >= 20 %. Dans ce seul cas, prolongation jusqu'a 189 seances avec un
plancher +10 % et un trailing de 20 % sous le sommet deja observe.
"""
from __future__ import annotations

import argparse, json
from dataclasses import dataclass
from math import ceil, floor
from pathlib import Path
import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k
from v182.hebdo.tabport_adaptive_stop_publish import _attribution
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import _period_trade_metrics, overall_summary
from v182.hebdo.tabport_publish import build_weekly_meta_signals, read_cache


@dataclass(frozen=True)
class ConvexExitConfig:
    initial_cash:float=65000.0; max_positions:int=12; max_position_eur:float=4500.0
    max_entries_month:int=5; max_entries_year:int=40; fee_rate:float=0.002; slippage_rate:float=0.001
    stop_pct:float=0.09; base_hold:int=126; extended_hold:int=189
    extension_trigger:float=0.20; profit_floor:float=0.10; trail_from_peak:float=0.20
    partial_exit_fraction:float=0.50


class ConvexExitRunner:
    def __init__(self,extend:bool,cfg:ConvexExitConfig|None=None,partial:bool=False): self.extend=bool(extend); self.partial=bool(partial); self.cfg=cfg or ConvexExitConfig()

    def run(self,signals:pd.DataFrame,prices:pd.DataFrame)->dict:
        s=Tabport65k._normalize_signals(signals); p=Tabport65k._normalize_prices(prices)
        if 'tier' in s.columns: s=s[s['tier'].isin(('TCT','CT_WATCH'))].copy()
        s=s[s['EV_net']>=0].copy()
        if s.empty: raise ValueError('BLOCK_CONVEX_EXIT: no eligible signals')
        price_dates=p.groupby('ticker')['date'].apply(list).to_dict(); last_price_date=p.groupby('ticker')['date'].max().to_dict()
        scheduled={}; skipped=[]
        for _,row in s.iterrows():
            nxt=next((d for d in price_dates.get(row['ticker'],[]) if d>row['date']),None)
            if nxt is None: skipped.append({'signal_date':row['date'],'ticker':row['ticker'],'reason':'NO_J1_BAR'})
            else: scheduled.setdefault(nxt,[]).append(row.to_dict())
        bars={d:g.set_index('ticker') for d,g in p.groupby('date',sort=True)}; dates=sorted(bars)
        cash=float(self.cfg.initial_cash); positions={}; ledger=[]; equity=[]; em={}; ey={}
        def close(ticker,date,reason,raw_exit):
            nonlocal cash
            pos=positions.pop(ticker); sell=float(raw_exit)*(1-self.cfg.slippage_rate); gross=sell*pos['shares']; fee=gross*self.cfg.fee_rate; cash+=gross-fee
            proceeds=(gross-fee)+pos['partial_proceeds']; pnl=proceeds-pos['cash_out']; exit_fee=fee+pos['partial_fee']; weighted_exit=(sell*pos['shares']+pos['partial_exit_value'])/pos['original_shares']
            ledger.append({'ticker':ticker,'signal_date':pos['signal_date'],'entry_date':pos['entry_date'],'exit_date':date,
                'shares':pos['original_shares'],'entry_price':pos['entry_price'],'exit_price':weighted_exit,'entry_fee':pos['entry_fee'],'exit_fee':exit_fee,'fees_total':pos['entry_fee']+exit_fee,
                'slippage_rate_side':self.cfg.slippage_rate,'cash_invested':pos['cash_out'],'pnl_net':pnl,'return_net':pnl/pos['cash_out'],'exit_reason':reason,
                'sessions_held':pos['sessions'],'mae':pos['mae'],'mfe':pos['mfe'],'EV_net_signal':pos['EV_net'],'stop_pct_signal':self.cfg.stop_pct,'extension_activated':pos['extended']})
        def mark(pos,bar):
            pos['last_close']=float(bar['close']); pos['mae']=min(pos['mae'],float(bar['low'])/pos['entry_price']-1); pos['mfe']=max(pos['mfe'],float(bar['high'])/pos['entry_price']-1); pos['peak']=max(pos['peak'],float(bar['high']))
        for date in dates:
            day=bars[date]
            for ticker in list(positions):
                if ticker not in day.index: continue
                pos=positions[ticker]; bar=day.loc[ticker]; pos['sessions']+=1
                if pos['extended']:
                    trail=max(pos['entry_price']*(1+self.cfg.profit_floor),pos['peak']*(1-self.cfg.trail_from_peak)); op=float(bar['open'])
                    if float(bar['low'])<=trail:
                        raw=op if op<trail else trail; close(ticker,date,'CONVEX_TRAIL_GAP' if raw<trail else 'CONVEX_TRAIL',raw); continue
                    mark(pos,bar)
                    if pos['sessions']>=self.cfg.extended_hold: close(ticker,date,'TIME_39W_CONVEX',float(bar['close']))
                    elif date==last_price_date[ticker]: close(ticker,date,'EOP_DATA_END',float(bar['close']))
                    continue
                mark(pos,bar); stop=pos['entry_price']*(1-self.cfg.stop_pct); op=float(bar['open'])
                if float(bar['low'])<=stop:
                    raw=op if op<stop else stop; close(ticker,date,'STOP_GAP_THROUGH' if raw<stop else 'STOP_-9%',raw)
                elif pos['sessions']>=self.cfg.base_hold:
                    ret=float(bar['close'])/pos['entry_price']-1
                    if self.extend and ret>=self.cfg.extension_trigger:
                        pos['extended']=True
                        if self.partial and pos['shares']>1:
                            sold=max(1,floor(pos['shares']*self.cfg.partial_exit_fraction)); sell=float(bar['close'])*(1-self.cfg.slippage_rate); gross=sold*sell; fee=gross*self.cfg.fee_rate
                            cash+=gross-fee; pos['shares']-=sold; pos['partial_proceeds']+=gross-fee; pos['partial_fee']+=fee; pos['partial_exit_value']+=sold*sell
                    else: close(ticker,date,'TIME_26W',float(bar['close']))
                elif date==last_price_date[ticker]: close(ticker,date,'EOP_DATA_END',float(bar['close']))
            candidates=sorted(scheduled.get(date,[]),key=lambda r:(-float(r['EV_net']),str(r['ticker'])))
            for sig in candidates:
                ticker=str(sig['ticker']); ym=(date.year,date.month)
                if ticker in positions: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'ALREADY_OPEN'}); continue
                if ticker not in day.index: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'NO_ENTRY_BAR'}); continue
                if len(positions)>=self.cfg.max_positions: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'MAX_POSITIONS'}); continue
                if em.get(ym,0)>=self.cfg.max_entries_month: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'MAX_ENTRIES_MONTH'}); continue
                if ey.get(date.year,0)>=self.cfg.max_entries_year: skipped.append({'signal_date':sig['date'],'ticker':ticker,'reason':'MAX_ENTRIES_YEAR'}); continue
                bar=day.loc[ticker]; buy=float(bar['open'])*(1+self.cfg.slippage_rate); affordable=min(self.cfg.max_position_eur,cash); shares=floor(affordable/(buy*(1+self.cfg.fee_rate)))
                if shares<1: continue
                gross=shares*buy; fee=gross*self.cfg.fee_rate; cash_out=gross+fee
                if cash_out>cash+1e-9: continue
                cash-=cash_out; pos={'signal_date':sig['date'],'entry_date':date,'shares':shares,'entry_price':buy,'entry_fee':fee,'cash_out':cash_out,'EV_net':float(sig['EV_net']),
                    'sessions':1,'mae':0.0,'mfe':0.0,'last_close':float(bar['close']),'peak':buy,'extended':False,'original_shares':shares,
                    'partial_proceeds':0.0,'partial_fee':0.0,'partial_exit_value':0.0}; positions[ticker]=pos; mark(pos,bar); em[ym]=em.get(ym,0)+1; ey[date.year]=ey.get(date.year,0)+1
                stop=buy*(1-self.cfg.stop_pct); op=float(bar['open'])
                if float(bar['low'])<=stop:
                    raw=op if op<stop else stop; close(ticker,date,'STOP_GAP_THROUGH' if raw<stop else 'STOP_-9%',raw)
                elif date==last_price_date[ticker]: close(ticker,date,'EOP_DATA_END',float(bar['close']))
            mv=sum(pos['shares']*pos['last_close'] for pos in positions.values()); equity.append({'date':date,'cash':cash,'market_value':mv,'equity':cash+mv,'open_positions':len(positions)})
        if positions: raise ValueError(f'BLOCK_CONVEX_EXIT: unclosed {sorted(positions)}')
        return {'ledger':pd.DataFrame(ledger),'equity':pd.DataFrame(equity).sort_values('date').reset_index(drop=True),'skipped':pd.DataFrame(skipped)}


def _year_metrics(ledger:pd.DataFrame,scenario:str)->pd.DataFrame:
    x=ledger.copy(); x['year']=pd.to_datetime(x['entry_date'],utc=True).dt.year.astype(str); rows=[]
    for year,g in x.groupby('year',sort=True): row={'scenario':scenario,'year':year}; row.update(_period_trade_metrics(g)); rows.append(row)
    return pd.DataFrame(rows)


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); ohlcv,_=read_cache(cache_dir)
    signals,_=build_weekly_meta_signals(ohlcv); feats=add_antifp_features(ohlcv[ohlcv['ticker'].astype(str).isin(set(signals['ticker'].astype(str)))].copy()); confirmed,audit=apply_j1_confirmation(signals,feats)
    market_dates=pd.Index(sorted(ohlcv['date'].unique())); cutoff=pd.Timestamp(market_dates[-190]); confirmed=confirmed[pd.to_datetime(confirmed['date'],utc=True)<=cutoff].copy()
    if confirmed.empty: raise ValueError('BLOCK_CONVEX_EXIT: no 189-session mature signals')
    confirmed.to_csv(out/'TABPORT_CONVEX_EXIT_SIGNALS_189_MATURES.csv',index=False); audit.to_csv(out/'TABPORT_CONVEX_EXIT_CONFIRM_AUDIT.csv',index=False)
    prices=ohlcv[['date','ticker','open','high','low','close']].copy(); results={}; summaries=[]; years=[]
    for name,extend,partial in [('BASE_TIME126',False,False),('EXTEND20_TRAIL20_FLOOR10',True,False),('HALF126_HALF_CONVEX',True,True)]:
        res=ConvexExitRunner(extend,partial=partial).run(confirmed,prices); results[name]=res; s=overall_summary(res['ledger'],res['equity'],65000); s['scenario']=name; summaries.append(s); years.append(_year_metrics(res['ledger'],name))
        d=out/name.lower(); d.mkdir(parents=True,exist_ok=True); res['ledger'].to_csv(d/'ledger.csv',index=False); res['equity'].to_csv(d/'nav.csv',index=False); res['skipped'].to_csv(d/'skipped.csv',index=False)
    comp=pd.DataFrame(summaries); comp.to_csv(out/'TABPORT_CONVEX_EXIT_COMPARISON.csv',index=False); year=pd.concat(years,ignore_index=True); year.to_csv(out/'TABPORT_CONVEX_EXIT_YEAR_STABILITY.csv',index=False)
    table,attr=_attribution(results['BASE_TIME126']['ledger'],results['HALF126_HALF_CONVEX']['ledger']); table.to_csv(out/'TABPORT_CONVEX_EXIT_ATTRIBUTION.csv',index=False)
    b,_,c=summaries; by=year[year.scenario=='BASE_TIME126'].set_index('year').pnl_net_eur; cy=year[year.scenario=='HALF126_HALF_CONVEX'].set_index('year').pnl_net_eur; yd=cy.sub(by,fill_value=0); required=ceil(.75*len(yd))
    checks={'return_improved':c['rendement_total_depuis_65000_pct']>b['rendement_total_depuis_65000_pct'],'pf_improved':c['profit_factor']>b['profit_factor'],
        'rr_improved':c['rr_payoff']>b['rr_payoff'],'expectancy_improved':c['esperance_pct']>b['esperance_pct'],'stops_not_increased':c['stops']<=b['stops'],
        'drawdown_not_worse':c['drawdown_max_pct']>=b['drawdown_max_pct'],'positive_without_top_candidate_only_trade':attr['robust_without_top_candidate_only_trade'],
        'year_stability_75pct':int((yd>0).sum())>=required}
    promoted=bool(all(checks.values())); diag={'status':'PUBLISHED','name':'TABPORT_CONVEX_EXIT_126_TO_189','retuning':False,'holdout_unlocked':False,'confirmed_signals_189_mature':int(len(confirmed)),
        'market_cutoff':str(cutoff),'rule':{'extension_trigger':.20,'profit_floor':.10,'trail_from_peak':.20,'base_hold':126,'extended_hold':189},'scenarios':summaries,
        'extended_trades':int(results['HALF126_HALF_CONVEX']['ledger']['extension_activated'].sum()),'attribution':attr,'year_pnl_deltas_eur':{str(k):float(v) for k,v in yd.items()},
        'checks':checks,'promoted':promoted,'decision':'PROMOTE_CONVEX_EXIT' if promoted else 'REJECT_KEEP_TIME126'}
    (out/'TABPORT_CONVEX_EXIT_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8'); print(json.dumps(diag,default=str)); return diag


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',default='data/cache/actions'); p.add_argument('--output-dir',default='outputs/tabport_convex_exit'); a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=='__main__': main()
