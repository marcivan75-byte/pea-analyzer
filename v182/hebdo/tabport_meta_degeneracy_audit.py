"""Quantify structural degeneracy of the current HEBDO AT META ranking.

Diagnostic only. Replays the governed chain and measures whether META/MAE/EV
components are trained/calibrated or are falling back to neutral constants.
No production rule is changed.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

from v182.hebdo.tabport_longitudinal_audit73 import load_governed_ohlcv
from v182.hebdo.tabport_publish import build_weekly_meta_signals


def _status_counts(s: pd.Series) -> dict:
    return {str(k): int(v) for k, v in s.fillna('MISSING').value_counts(dropna=False).to_dict().items()}


def run(pre2023: Path, manifest: Path, holdout_cache: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_governed_ohlcv(pre2023, manifest, holdout_cache)
    signals, signal_audit = build_weekly_meta_signals(ohlcv)
    x = signals.copy()
    x['date'] = pd.to_datetime(x['date'], utc=True)
    x['segment'] = np.where(x['date'] < pd.Timestamp('2023-01-01', tz='UTC'), 'DEVELOPMENT_2010_2022', 'HOLDOUT_2023_2026')
    x['EV_net'] = pd.to_numeric(x['EV_net'], errors='coerce')
    x['prob_meta'] = pd.to_numeric(x['prob_meta'], errors='coerce')
    x['prob_stop_9'] = pd.to_numeric(x['prob_stop_9'], errors='coerce')
    if x[['EV_net','prob_meta','prob_stop_9']].isna().any().any():
        raise ValueError('BLOCK_META_DEGENERACY_NONNUMERIC')

    rows=[]
    for seg,g in x.groupby('segment', sort=False):
        rows.append({
            'segment': seg,
            'signals': int(len(g)),
            'decision_dates': int(g['date'].nunique()),
            'prob_meta_unique': int(g['prob_meta'].nunique()),
            'prob_meta_eq_0_5_pct': float((np.isclose(g['prob_meta'],0.5,atol=1e-12)).mean()*100),
            'ev_unique': int(g['EV_net'].nunique()),
            'ev_eq_0_044_pct': float((np.isclose(g['EV_net'],0.044,atol=1e-12)).mean()*100),
            'ev_std': float(g['EV_net'].std(ddof=0)),
            'prob_stop_unique': int(g['prob_stop_9'].nunique()),
        })
    segment_df=pd.DataFrame(rows)

    grp_rows=[]
    for d,g in x.groupby('date'):
        counts=g['EV_net'].round(12).value_counts()
        top_count=int(counts.iloc[0]) if len(counts) else 0
        grp_rows.append({
            'date': d,
            'n': int(len(g)),
            'ev_unique': int(g['EV_net'].round(12).nunique()),
            'largest_ev_tie_n': top_count,
            'largest_ev_tie_pct': float(100*top_count/len(g)) if len(g) else 0.0,
            'all_prob_meta_0_5': bool(np.isclose(g['prob_meta'],0.5,atol=1e-12).all()),
        })
    groups=pd.DataFrame(grp_rows)
    dev_groups=groups[groups['date'] < pd.Timestamp('2023-01-01',tz='UTC')]

    summary={
        'status':'SUCCESS',
        'version':'TABPORT_META_DEGENERACY_AUDIT_V1',
        'diagnostic_only':True,
        'production_change':False,
        'quality':quality,
        'signal_audit':signal_audit,
        'meta_model_status':_status_counts(x['meta_model_status']),
        'mae_model_status':_status_counts(x['mae_model_status']),
        'ev_model_status':_status_counts(x['ev_model_status']),
        'selection_confidence':_status_counts(x['selection_confidence']),
        'development':{
            'signals':int((x['segment']=='DEVELOPMENT_2010_2022').sum()),
            'prob_meta_eq_0_5_pct':float((np.isclose(x.loc[x['segment']=='DEVELOPMENT_2010_2022','prob_meta'],0.5,atol=1e-12)).mean()*100),
            'decision_groups_all_prob_meta_0_5_pct':float(dev_groups['all_prob_meta_0_5'].mean()*100) if len(dev_groups) else None,
            'median_largest_ev_tie_pct':float(dev_groups['largest_ev_tie_pct'].median()) if len(dev_groups) else None,
            'groups_with_ev_tie_ge_50pct':int((dev_groups['largest_ev_tie_pct']>=50).sum()) if len(dev_groups) else 0,
        },
        'mechanical_default_ev_net':0.044,
        'mechanical_default_ev_derivation':'0.5*0.14 + 0.3*(-0.09) + 0.2*0.02 - 0.003 = 0.044',
    }
    segment_df.to_csv(output_dir/'TABPORT_META_DEGENERACY_SEGMENTS.csv',index=False)
    groups.to_csv(output_dir/'TABPORT_META_DEGENERACY_BY_DECISION.csv',index=False)
    x[['date','ticker','segment','prob_meta','meta_model_status','prob_stop_9','mae_model_status','EV_net','ev_model_status','selection_confidence']].to_csv(output_dir/'TABPORT_META_DEGENERACY_SIGNALS.csv',index=False)
    (output_dir/'TABPORT_META_DEGENERACY_SUMMARY.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf-8')
    print(json.dumps(summary,indent=2,default=str))
    print(segment_df.to_csv(index=False))
    return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--pre2023',required=True); p.add_argument('--manifest',required=True); p.add_argument('--holdout-cache',required=True); p.add_argument('--output-dir',required=True)
    a=p.parse_args(); run(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.output_dir))

if __name__=='__main__': main()
