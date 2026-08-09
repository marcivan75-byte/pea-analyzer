from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
CONFIG=ROOT/'data/reference/V21.0_ACTIONS_PEA_CONFIG.json'
FUNNEL=ROOT/'data/reference/V21.0_ACTIONS_FUNNEL_CONFIG.json'
TARGET=ROOT/'outputs/V21.0_ACTIONS_PEA_1429_PREPARED.csv'
AUDIT=ROOT/'outputs/audit/V21.0_ACTIONS_PRECOMMIT_AUDIT.json'


def run(root: Path|None=None)->dict:
    root=root or ROOT
    cfg=json.loads((root/CONFIG.relative_to(ROOT)).read_text(encoding='utf-8'))
    fun=json.loads((root/FUNNEL.relative_to(ROOT)).read_text(encoding='utf-8'))
    df=pd.read_csv(root/TARGET.relative_to(ROOT),sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
    errors=[]; warnings=[]
    if len(df)!=1429 or df['isin'].astype(str).nunique()!=1429: errors.append('canonical_universe_not_1429')
    if not df['asset_class'].astype(str).str.upper().eq('ACTION').all(): errors.append('non_action_rows_present')
    if not df['execution'].astype(str).eq('RESEARCH_ONLY').all(): errors.append('execution_guard_failed')
    if df['isin'].duplicated().any(): errors.append('duplicate_isin')

    weight_sums={}
    all_weighted=set()
    for hz,w in cfg['horizon_weights'].items():
        weight_sums[hz]=round(sum(float(x) for x in w.values()),10); all_weighted |= set(w)
        if abs(weight_sums[hz]-1)>1e-9: errors.append(f'{hz}_weights_not_100')
    weight_sums['SHORT']=round(sum(float(x) for x in cfg['short_weights'].values()),10); all_weighted |= set(cfg['short_weights'])
    if abs(weight_sums['SHORT']-1)>1e-9: errors.append('SHORT_weights_not_100')
    td_sum=round(sum(float(x) for x in fun['context_weights'].values()),10)
    if abs(td_sum-1)>1e-9: errors.append('TOPDOWN_weights_not_100')

    missing_fields=sorted(x for x in all_weighted if x not in df.columns)
    if missing_fields: errors.append('missing_weighted_fields:'+','.join(missing_fields))
    forbidden=[x for x in all_weighted if x.startswith(('score_','decision_','rank_','selection_','action_topdown_','funnel_','smart_money_','action_smart_money_'))]
    if forbidden: errors.append('double_count_or_output_inputs:'+','.join(sorted(forbidden)))

    def observed(field):
        if field=='distribution_policy': return df[field].astype(str).str.upper().isin({'DIST','ACC','ACC_OR_DIST'})
        if field in {'positive_reversal_flag','stoch_bull_cross_flag','stoch_bear_cross_flag','breakout_20d_flag'}:
            return df[field].notna() & ~df[field].astype(str).str.lower().isin({'nan','none','<na>',''})
        return pd.to_numeric(df[field],errors='coerce').notna()
    field_coverage={f:round(float(observed(f).mean()),4) for f in sorted(all_weighted) if f in df.columns}
    sparse={f:c for f,c in field_coverage.items() if c<0.10}
    if sparse: warnings.append('Sparse weighted fields are allowed only through observed-weight renormalization: '+','.join(sparse))

    # Coverage by horizon, calculated directly on raw evidence; false booleans count as observed.
    row_cov={}
    for hz,w in {**cfg['horizon_weights'],'SHORT':cfg['short_weights']}.items():
        denom=pd.Series(0.0,index=df.index)
        for f,wt in w.items():
            if f=='distribution_policy': obs=df[f].astype(str).str.upper().isin({'DIST','ACC','ACC_OR_DIST'})
            elif f in {'positive_reversal_flag','stoch_bull_cross_flag','stoch_bear_cross_flag'}: obs=df[f].notna() & ~df[f].astype(str).str.lower().isin({'nan','none',''})
            else: obs=pd.to_numeric(df[f],errors='coerce').notna()
            denom += obs.astype(float)*float(wt)
        row_cov[hz]=round(float(denom.mean()),4)

    pea_high=df['pea_confidence'].astype(str).str.upper().str.startswith('HIGH')
    identity=pd.to_numeric(df['v182_ticker_validation_confidence_pct'],errors='coerce')/100.0
    audit={
      'passed':not errors,'version':cfg['version'],'rows':len(df),'unique_isin':int(df['isin'].nunique()),
      'columns':len(df.columns),'weight_sums':weight_sums,'topdown_weight_sum':td_sum,
      'missing_data_policy':cfg['missing_data_policy'],'neutral_50_imputation_allowed':False,
      'weighted_field_coverage':field_coverage,'sparse_weighted_fields':sparse,'mean_weighted_input_coverage':row_cov,
      'pea_high_confidence_rows':int(pea_high.sum()),'pea_review_only_rows':int((~pea_high).sum()),
      'identity_pass_rows':int(identity.ge(float(cfg['coverage']['identity_min'])).sum()),
      'smart_money_positive_score_boost_allowed':cfg['smart_money']['positive_score_boost_allowed'],
      'topdown_separated_from_horizon_weights':not any(x.startswith(('action_topdown_','funnel_')) for x in all_weighted),
      'errors':errors,'warnings':warnings,'generated_at_utc':datetime.now(timezone.utc).isoformat()
    }
    ap=root/AUDIT.relative_to(ROOT); ap.parent.mkdir(parents=True,exist_ok=True); ap.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if errors: raise RuntimeError('V21 precommittee audit failed: '+str(errors))
    print('V21_ACTIONS_PRECOMMIT_AUDIT_OK',{'coverage':row_cov,'sparse':len(sparse),'columns':len(df.columns)})
    return audit

if __name__=='__main__': run()
