from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
from pandas.tseries.offsets import BDay

INITIAL=65000.0; MAX_LINE=4500.0; MAX_LINES=12; FEE=.002; SLIP_STRESS=.001; BIG=.15; RR_TARGET=3.3
TARGET_WINDOWS=(20,63,126)


def num(s): return pd.to_numeric(s,errors='coerce')

def build(df):
    x=pd.DataFrame(index=df.index); x['date']=pd.to_datetime(df['as_of_date'],errors='coerce')
    x['ticker']=df['ticker'].astype(str); x['isin']=df['isin'].astype(str); x['ret26']=num(df['forward_ret_true_26w']); x['stop']=df['hit_stop'].astype('boolean')
    x['gov']=num(df['governed_score']); mom=num(df['mom_26w']); dd=num(df['drawdown_4w']); atr=num(df['atr_14_pct']); close=num(df['close']); sma200=num(df['sma200'])
    x['momdd']=mom*(1-dd.abs()); x['vold']=atr*dd.abs(); x['trend']=close/sma200-1.; x['atr']=atr; x['close']=close
    for c in ['entry_date','entry_price','label_end_date_26w','day_stop','stop_pct_used','mae','mfe']:
        x[c]=df[c] if c in df.columns else np.nan
    good=x.date.notna()&x.ret26.notna()&x.stop.notna()
    for c in ['gov','momdd','vold','trend','atr','close']: good &= np.isfinite(x[c])
    x=x.loc[good].copy(); x['stop']=x.stop.astype(bool)
    return x.sort_values(['date','ticker','isin'],kind='stable')

def ecdf(ref, vals):
    r=np.sort(np.asarray(ref,dtype=float)); v=np.asarray(vals,dtype=float)
    return np.searchsorted(r,v,side='right')/max(len(r),1)

def capacity(g,col):
    z=g.copy(); z['month']=z.date.dt.to_period('M'); z['year']=z.date.dt.year
    z=z.sort_values(['month',col,'date','ticker'],ascending=[True,False,True,True],kind='stable').groupby('month',group_keys=False).head(5)
    z=z.sort_values(['year',col,'date','ticker'],ascending=[True,False,True,True],kind='stable').groupby('year',group_keys=False).head(40)
    return z.sort_values(['date',col,'ticker'],ascending=[True,False,True],kind='stable')

def attach_rr(selected, parquet):
    p=pd.read_parquet(parquet); p.columns=[str(c).strip().lower() for c in p.columns]
    dc=next((c for c in ('date','market_data_date','as_of_date') if c in p.columns),None)
    if dc is None or 'isin' not in p.columns or 'high' not in p.columns: raise SystemExit('BLOCK_FINAL_RR_DATA')
    wanted=set(selected['isin'].astype(str)); p=p[p['isin'].astype(str).isin(wanted)].copy()
    p['date']=pd.to_datetime(p[dc],errors='coerce').dt.normalize(); p['high']=num(p['high']); p=p.dropna(subset=['isin','date','high']).sort_values(['isin','date'],kind='stable').drop_duplicates(['isin','date'],keep='last')
    mins={20:10,63:20,126:40}
    for w in TARGET_WINDOWS: p[f'ph{w}']=p.groupby('isin',sort=False)['high'].transform(lambda s,w=w:s.shift(1).rolling(w,min_periods=mins[w]).max())
    s=selected.copy(); s['_id']=np.arange(len(s)); s['d0']=pd.to_datetime(s.date).dt.normalize()
    left=s[['_id','isin','d0']].sort_values(['d0','isin'],kind='stable'); right=p[['isin','date','ph20','ph63','ph126']].sort_values(['date','isin'],kind='stable')
    m=pd.merge_asof(left,right,left_on='d0',right_on='date',by='isin',direction='backward',allow_exact_matches=True)
    s=s.merge(m[['_id','ph20','ph63','ph126']],on='_id',how='left',validate='one_to_one').drop(columns=['_id','d0'])
    cand=pd.DataFrame(index=s.index)
    for w in TARGET_WINDOWS:
        t=num(s[f'ph{w}']); cand[str(w)]=t.where(np.isfinite(t)&(t>s.close))
    target=cand.min(axis=1,skipna=True); stop=num(s.stop_pct_used).where(num(s.stop_pct_used)>0,.09)
    s['rr_ex_ante']=(target/s.close-1.)/stop; s['rr_target_valid']=target.notna(); return s

def signal_metrics(g):
    r=g.ret26.astype(float); w=r[r>0]; l=r[r<=0]; gl=float((-l).sum()); rr=num(g.rr_ex_ante).dropna(); mae=num(g.mae).dropna(); mfe=num(g.mfe).dropna()
    return {'signals':int(len(g)),'win_rate':float((r>0).mean()),'false_positive_rate_ret_le_0':float((r<=0).mean()),'stop_rate':float(g.stop.mean()),'expectancy':float(r.mean()),'profit_factor':float(w.sum()/gl) if gl>0 else None,'avg_gain':float(w.mean()) if len(w) else None,'avg_loss':float(l.mean()) if len(l) else None,'gains_count':int(len(w)),'losses_count':int(len(l)),'big_winners_ge15_count':int((r>=BIG).sum()),'rr_coverage':float(g.rr_ex_ante.notna().mean()),'rr_mean':float(rr.mean()) if len(rr) else None,'rr_median':float(rr.median()) if len(rr) else None,'rr_ge_3_3_share':float((rr>=RR_TARGET).mean()) if len(rr) else None,'mae_mean_reporting_only':float(mae.mean()) if len(mae) else None,'mae_median_reporting_only':float(mae.median()) if len(mae) else None,'mfe_mean_reporting_only':float(mfe.mean()) if len(mfe) else None,'mfe_median_reporting_only':float(mfe.median()) if len(mfe) else None}

def simulate(cands,slip):
    cash=INITIAL; pos=[]; trades=[]; events=[]; rejected=0
    c=cands.copy(); c['entry_date']=pd.to_datetime(c.entry_date); c['label_end_date_26w']=pd.to_datetime(c.label_end_date_26w); c=c.sort_values(['entry_date','ENS50','ticker'],ascending=[True,False,True],kind='stable')
    def close(now):
        nonlocal cash,pos
        keep=[]
        for p in pos:
            if p['exit_date']<=now:
                px=p['raw']*(1+p['ret26'])*(1-slip); proceeds=p['shares']*px; fee=proceeds*FEE; cash+=proceeds-fee; p=dict(p); p['exit_price_exec']=px; p['exit_fee']=fee; p['fees_eur']=p['entry_fee']+fee; p['net_pnl_eur']=proceeds-fee-p['cash_out']; trades.append(p)
            else: keep.append(p)
        pos=keep
    for _,r in c.iterrows():
        now=pd.Timestamp(r.entry_date); close(now); invested=sum(p['cash_out'] for p in pos); events.append({'date':now,'equity_eur':cash+invested,'open_lines':len(pos),'invested_eur':invested})
        if len(pos)>=MAX_LINES: rejected+=1; continue
        raw=float(r.entry_price); ep=raw*(1+slip); per=ep*(1+FEE); shares=int(np.floor(min(MAX_LINE,cash)/per))
        if shares<1: rejected+=1; continue
        notional=shares*ep; ef=notional*FEE; co=notional+ef; cash-=co
        ex=now+BDay(max(int(r.day_stop),0)) if bool(r.stop) and pd.notna(r.day_stop) else pd.Timestamp(r.label_end_date_26w)
        pos.append({'signal_date':pd.Timestamp(r.date),'entry_date':now,'exit_date':ex,'ticker':r.ticker,'isin':r.isin,'shares':shares,'raw':raw,'entry_price_exec':ep,'entry_fee':ef,'cash_out':co,'ret26':float(r.ret26),'stop':bool(r.stop),'rr_ex_ante':None if pd.isna(r.rr_ex_ante) else float(r.rr_ex_ante)})
    for d in sorted({p['exit_date'] for p in pos}): close(pd.Timestamp(d)); invested=sum(p['cash_out'] for p in pos); events.append({'date':pd.Timestamp(d),'equity_eur':cash+invested,'open_lines':len(pos),'invested_eur':invested})
    t=pd.DataFrame(trades).sort_values(['exit_date','entry_date'],kind='stable'); e=pd.DataFrame(events).sort_values('date',kind='stable').drop_duplicates('date',keep='last')
    pnl=t.net_pnl_eur.astype(float); gp=float(pnl[pnl>0].sum()); gl=float((-pnl[pnl<=0]).sum()); eq=e.equity_eur.astype(float); dd=eq/eq.cummax()-1.; years=max((e.date.iloc[-1]-e.date.iloc[0]).days/365.25,1/365.25)
    m={'trades':int(len(t)),'net_eur':float(cash-INITIAL),'net_pct':float(cash/INITIAL-1),'cagr':float((cash/INITIAL)**(1/years)-1),'profit_factor':float(gp/gl) if gl>0 else None,'win_rate':float((pnl>0).mean()),'max_drawdown_event_proxy':float(dd.min()),'fees_eur':float(t.fees_eur.sum()),'avg_capital_utilization_event_proxy':float((e.invested_eur/INITIAL).mean()),'max_open_lines':int(e.open_lines.max()),'rejected_capacity_or_cash':int(rejected)}
    return t,e,m

def period_table(t,freq):
    if t.empty:return pd.DataFrame()
    z=t.copy(); d=pd.to_datetime(z.exit_date); z['period']=d.dt.to_period(freq).astype(str); rows=[]
    for p,g in z.groupby('period',sort=True):
        pnl=g.net_pnl_eur.astype(float); gp=float(pnl[pnl>0].sum()); gl=float((-pnl[pnl<=0]).sum())
        rows.append({'period':p,'trades':len(g),'net_eur':float(pnl.sum()),'net_pct_initial_capital':float(pnl.sum()/INITIAL),'profit_factor':float(gp/gl) if gl>0 else None,'win_rate':float((pnl>0).mean()),'stops':int(g.stop.sum()),'fees_eur':float(g.fees_eur.sum())})
    return pd.DataFrame(rows)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--train',type=Path,required=True); ap.add_argument('--holdout',type=Path,required=True); ap.add_argument('--price-parquet',type=Path,required=True); ap.add_argument('--frozen-config',type=Path,required=True); ap.add_argument('--pass5-report',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    cfg=json.loads(a.frozen_config.read_text()); p5=json.loads(a.pass5_report.read_text())
    if cfg.get('holdout_accessed_during_selection') is not False or cfg.get('ensemble')!='ENS50': raise SystemExit('BLOCK_FINAL_GOVERNANCE')
    tr=build(pd.read_csv(a.train,low_memory=False)); ho=build(pd.read_csv(a.holdout,low_memory=False)); valid=tr.iloc[int(len(tr)*.80):].copy()
    risk_ref=-valid.momdd.to_numpy(float); valid=valid.loc[ecdf(risk_ref,-valid.momdd)<=float(cfg['pass2_filter']['H_MOM_DD_risk_keep_level'])].copy(); ho=ho.loc[ecdf(risk_ref,-ho.momdd)<=float(cfg['pass2_filter']['H_MOM_DD_risk_keep_level'])].copy()
    ho['pgov']=ecdf(valid.gov,ho.gov); ho['pvold_good']=1-ecdf(valid.vold,ho.vold); ho['STATIC']=.92*ho.pgov+.08*ho.pvold_good
    th=cfg['regime_thresholds']; wr=.08+np.where(ho.trend<=float(th['trend_q33']),.02,0.)+np.where(ho.atr>=float(th['atr_q67']),.02,0.); wr=np.clip(wr,.08,.12); ho['ADAPT']=(1-wr)*ho.pgov+wr*ho.pvold_good; ho['ENS50']=.5*ho.STATIC+.5*ho.ADAPT
    sel=capacity(ho,'ENS50'); sel=attach_rr(sel,a.price_parquet)
    base_t,base_e,base_m=simulate(sel,0.); stress_t,stress_e,stress_m=simulate(sel,SLIP_STRESS); sm=signal_metrics(sel)
    annual=period_table(base_t,'Y'); quarterly=period_table(base_t,'Q')
    global_row={**{f'signal_{k}':v for k,v in sm.items()},**{f'portfolio_{k}':v for k,v in base_m.items()},'stress_net_eur':stress_m['net_eur'],'stress_cagr':stress_m['cagr'],'stress_profit_factor':stress_m['profit_factor'],'stress_max_drawdown_event_proxy':stress_m['max_drawdown_event_proxy'],'rr_objective_3_3_met_on_mean':bool(sm['rr_mean'] is not None and sm['rr_mean']>RR_TARGET),'rr_objective_3_3_met_on_median':bool(sm['rr_median'] is not None and sm['rr_median']>RR_TARGET),'net_15pct_per_year_objective_met':bool(base_m['cagr']>.15)}
    out=a.out_dir; out.mkdir(parents=True,exist_ok=True); pd.DataFrame([global_row]).to_csv(out/'TAB1_GLOBAL.csv',index=False); annual.to_csv(out/'TAB1_ANNUAL.csv',index=False); quarterly.to_csv(out/'TAB1_QUARTERLY.csv',index=False); sel.to_csv(out/'TABPORT_HOLDOUT_SELECTED.csv',index=False); base_t.to_csv(out/'TABPORT_TRADES_BASE.csv',index=False); stress_t.to_csv(out/'TABPORT_TRADES_STRESS.csv',index=False); base_e.to_csv(out/'TABPORT_EQUITY_BASE.csv',index=False)
    report={'version':'TABPORT_V22_1_FINAL_HOLDOUT_1','governance':{'selection_frozen_before_holdout':True,'holdout_period':'2023-2026','holdout_used_for_tuning':False,'rank_calibration':'PRE2023_VALIDATION_EMPIRICAL_CDF','embargo_weeks':26,'anti_lookahead':True,'survivorship_bias_disclosure':'Historical universe is governed but residual survivorship bias cannot be claimed absent without a delisting-complete point-in-time membership archive.'},'signal_global':sm,'portfolio_base':base_m,'portfolio_stress':stress_m,'pre2023_pass5_reference':p5.get('base'),'objectives':{'RR_gt_3_3_mean':bool(sm['rr_mean'] is not None and sm['rr_mean']>RR_TARGET),'RR_gt_3_3_median':bool(sm['rr_median'] is not None and sm['rr_median']>RR_TARGET),'net_gt_15pct_per_year':bool(base_m['cagr']>.15)},'drawdown_disclosure':'Max drawdown is an event-level realized/cost-basis proxy because daily mark-to-market for the selected portfolio is not present in the governed ledger.','mae_mfe_disclosure':'MAE/MFE are used only after frozen selection for reporting, never as model inputs.'}
    (out/'TABPORT_FINAL_REPORT.json').write_text(json.dumps(report,indent=2,sort_keys=True)); print(json.dumps(report,indent=2,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
