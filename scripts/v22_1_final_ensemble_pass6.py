from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

HOLDOUT_START = pd.Timestamp('2023-01-01')
EMBARGO = pd.Timedelta(weeks=26)
BIG_WIN = 0.15
MAX_PER_MONTH = 5
MAX_PER_YEAR = 40


def num(df, c):
    if c not in df.columns:
        raise SystemExit(f'BLOCK_PASS6_DATA: missing {c}')
    return pd.to_numeric(df[c], errors='coerce')


def build(df):
    x = pd.DataFrame(index=df.index)
    x['date'] = pd.to_datetime(df['as_of_date'], errors='coerce')
    x['ticker'] = df['ticker'].astype(str)
    x['isin'] = df['isin'].astype(str)
    x['ret26'] = num(df, 'forward_ret_true_26w')
    x['stop'] = df['hit_stop'].astype('boolean')
    x['gov'] = num(df, 'governed_score')
    mom = num(df, 'mom_26w'); dd = num(df, 'drawdown_4w'); atr = num(df, 'atr_14_pct')
    close = num(df, 'close'); sma200 = num(df, 'sma200')
    x['momdd'] = mom * (1.0 - dd.abs())
    x['vold'] = atr * dd.abs()
    x['trend'] = close / sma200 - 1.0
    x['atr'] = atr
    good = x['date'].notna() & x['ret26'].notna() & x['stop'].notna()
    for c in ['gov','momdd','vold','trend','atr']:
        good &= np.isfinite(x[c])
    x = x.loc[good].copy(); x['stop'] = x['stop'].astype(bool)
    return x.sort_values(['date','ticker','isin'], kind='stable')


def met(g):
    if g.empty:
        return {'n':0,'stop_rate':None,'expectancy':None,'profit_factor':None,'win_rate':None,'big_winners':0}
    r = g.ret26.astype(float); w = r[r>0]; l = r[r<=0]; gl = float((-l).sum())
    return {'n':int(len(g)),'stop_rate':float(g.stop.mean()),'expectancy':float(r.mean()),'profit_factor':float(w.sum()/gl) if gl>0 else None,'win_rate':float((r>0).mean()),'big_winners':int((r>=BIG_WIN).sum())}


def cap(g, col):
    z = g.copy(); z['month'] = z.date.dt.to_period('M'); z['year'] = z.date.dt.year
    z = z.sort_values(['month',col,'date','ticker'], ascending=[True,False,True,True], kind='stable').groupby('month',group_keys=False).head(MAX_PER_MONTH)
    z = z.sort_values(['year',col,'date','ticker'], ascending=[True,False,True,True], kind='stable').groupby('year',group_keys=False).head(MAX_PER_YEAR)
    return z.sort_values(['date',col,'ticker'], ascending=[True,False,True], kind='stable')


def subperiod(date):
    y = date.year
    if y <= 2019: return '2018-2019'
    if y == 2020: return '2020_STRESS'
    return '2021-2022H1'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--train', type=Path, required=True)
    ap.add_argument('--pass4-report', type=Path, required=True)
    ap.add_argument('--pass5-report', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, required=True)
    a = ap.parse_args()

    p4 = json.loads(a.pass4_report.read_text(encoding='utf-8'))
    p5 = json.loads(a.pass5_report.read_text(encoding='utf-8'))
    if p4.get('governance',{}).get('holdout_accessed') is not False or p5.get('governance',{}).get('holdout_accessed') is not False:
        raise SystemExit('BLOCK_PASS6_GOVERNANCE: upstream holdout seal not proven')
    if p4.get('selected',{}).get('variant') != 'RISK_ADD_ADAPT':
        raise SystemExit('BLOCK_PASS6_GOVERNANCE: pass4 not frozen')
    if p5.get('robustness',{}).get('slippage_dependency_flag') is not False:
        raise SystemExit('BLOCK_PASS6_GOVERNANCE: pass5 slippage robustness failed')

    x = build(pd.read_csv(a.train, low_memory=False)); cutoff = HOLDOUT_START - EMBARGO
    if x.empty or x.date.max() >= cutoff:
        raise SystemExit('BLOCK_PASS6_EMBARGO')
    valid = x.iloc[int(len(x)*.80):].copy()
    valid = valid.loc[(-valid.momdd).rank(pct=True) <= .90].copy()

    th = p4['regime_thresholds']
    tq1 = float(th['trend_q33']); tq2 = float(th['trend_q67']); aq1 = float(th['atr_q33']); aq2 = float(th['atr_q67'])
    valid['pgov'] = valid.gov.rank(pct=True); valid['pvold_good'] = 1.0 - valid.vold.rank(pct=True)
    valid['STATIC'] = .92*valid.pgov + .08*valid.pvold_good
    wr = .08 + np.where(valid.trend<=tq1,.02,0.0) + np.where(valid.atr>=aq2,.02,0.0)
    wr = np.clip(wr,.08,.12)
    valid['ADAPT'] = (1-wr)*valid.pgov + wr*valid.pvold_good
    # Small, pre-declared ensemble family only. No holdout search and no broad weight grid.
    valid['ENS50'] = .50*valid['STATIC'] + .50*valid['ADAPT']
    valid['ENS75'] = .25*valid['STATIC'] + .75*valid['ADAPT']

    sels = {v:cap(valid,v) for v in ['ADAPT','ENS50','ENS75']}
    base = sels['ADAPT']; bm = met(base); basebig = max(bm['big_winners'],1)
    rows=[]; stability_rows=[]; best=None
    for v in ['ENS50','ENS75']:
        s=sels[v].copy(); m=met(s); recall=m['big_winners']/basebig
        s['subperiod']=s.date.map(subperiod)
        subs=[]
        for name,g in s.groupby('subperiod',sort=True):
            mm=met(g); subs.append({'variant':v,'subperiod':name,**mm}); stability_rows.append({'variant':v,'subperiod':name,**mm})
        required={'2018-2019','2020_STRESS','2021-2022H1'}
        present={q['subperiod'] for q in subs}
        stable = required.issubset(present) and all((q['expectancy'] or -1)<=0.0 for q in []) is True
        stable = stable and all(q['expectancy'] is not None and q['expectancy']>0 and q['profit_factor'] is not None and q['profit_factor']>1.0 for q in subs)
        guard = recall>=.90 and m['expectancy']>=bm['expectancy']*.90 and m['profit_factor']>=bm['profit_factor']*.90 and m['stop_rate']<=bm['stop_rate']+.01 and stable
        worst_exp=min(q['expectancy'] for q in subs) if subs else -1e9
        worst_pf=min(q['profit_factor'] for q in subs if q['profit_factor'] is not None) if subs else 0.0
        row={'variant':v,'admissible':bool(guard),'big_winner_recall_vs_adapt':float(recall),'worst_subperiod_expectancy':float(worst_exp),'worst_subperiod_pf':float(worst_pf),**m}
        rows.append(row)
        key=(float(worst_exp),float(worst_pf),-float(m['stop_rate']),float(m['expectancy']))
        if guard and (best is None or key>best[0]): best=(key,v,row)

    if best is None:
        raise SystemExit('BLOCK_PASS6_STABILITY: no final ensemble passes all pre2023 stability guards')

    final_v=best[1]; final_sel=sels[final_v]
    out=a.out_dir; out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(out/'PASS6_ENSEMBLE_VARIANTS.csv',index=False)
    pd.DataFrame(stability_rows).to_csv(out/'PASS6_STABILITY_SUBPERIODS.csv',index=False)
    final_sel.to_csv(out/'PASS6_FINAL_SELECTED_PRE2023.csv',index=False)
    frozen={
      'model':'TABPORT_V22_1_FINAL', 'ensemble':final_v,
      'ensemble_formula': '.50*STATIC+.50*ADAPT' if final_v=='ENS50' else '.25*STATIC+.75*ADAPT',
      'pass2_filter':{'H_MOM_DD_risk_keep_level':.90},
      'static_formula':'.92*rank(governed_score)+.08*(1-rank(H_VOL_DD))',
      'adaptive_weight_formula':'0.08 + 0.02 if trend<=q33 + 0.02 if atr>=q67; clipped [0.08,0.12]',
      'regime_thresholds':th,
      'capacity':{'max_entries_month':5,'max_entries_year':40},
      'execution':p5['execution'],
      'holdout_accessed_during_selection':False,
      'embargo_weeks':26,
    }
    (out/'PASS6_FROZEN_CONFIG.json').write_text(json.dumps(frozen,indent=2,sort_keys=True),encoding='utf-8')
    report={'version':'V22.1_TABPORT_PASS6_FINAL_ENSEMBLE_1','governance':{'holdout_accessed':False,'holdout_scope':'SEALED_UNTIL_THIS_PASS_SUCCEEDS_THEN_FINAL_EVALUATION_ONLY','training_source':'PRE_2023_PIT_ONLY','embargo_weeks':26,'train_max_date':str(x.date.max().date()),'survivorship_bias_disclosure_required':True},'adapt_reference':bm,'selected':{'variant':final_v,'metrics':best[2]},'ensemble_variants':rows,'stability_subperiods':stability_rows,'frozen_config':'PASS6_FROZEN_CONFIG.json','pass5_robustness_reference':p5['robustness'],'promotion_automatic':False}
    (out/'PASS6_REPORT.json').write_text(json.dumps(report,indent=2,sort_keys=True),encoding='utf-8')
    print(json.dumps(report,indent=2,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
