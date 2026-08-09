from __future__ import annotations

from datetime import datetime, timezone
import json, time
import pandas as pd

from v182.decision.actions_final_reference_1829 import (
    BASE, CAND, OUT, AUDIT, EXTRA_COLS, enrich_candidate, write_xlsx
)


def main() -> None:
    now=datetime.now(timezone.utc).isoformat()
    base=pd.read_csv(BASE,sep=';',dtype=object,encoding='utf-8-sig')
    cand=pd.read_csv(CAND,sep=';',dtype=object,encoding='utf-8-sig')
    if len(base)!=1429 or base['isin'].astype(str).nunique()!=1429:
        raise RuntimeError('Base 1429 invalid')
    if len(cand)!=400 or cand['isin'].astype(str).nunique()!=400 or not cand['status'].eq('INTEGRER').all():
        raise RuntimeError('Candidate set invalid')
    overlap=set(base['isin'].astype(str).str.upper()) & set(cand['isin'].astype(str).str.upper())
    if overlap:
        raise RuntimeError(f'Overlap between canonical and candidates: {len(overlap)}')

    schema=list(base.columns)
    schema += [c for c in EXTRA_COLS if c not in schema]
    missing=[c for c in EXTRA_COLS if c not in base.columns]
    if missing:
        base=pd.concat([base,pd.DataFrame('',index=base.index,columns=missing)],axis=1)
    base['canonical_universe']='PEA_ACTIONS_FINAL'
    base['canonical_reference_rule']='1429_CANONICAL_PLUS_400_VALIDATED_QUARANTINE'
    base['canonical_execution_guard']='NO_LIVE_EXECUTION'
    base['final_reference_status']='RETAINED'
    base['final_reference_origin']='CANONICAL_1429'
    base['final_reference_as_of']=now

    rows=[]
    for i,(_,r) in enumerate(cand.iterrows(),1):
        rows.append(enrich_candidate(r,schema,now))
        if i%25==0:
            print('enriched',i,flush=True)
        time.sleep(0.04)
    add=pd.DataFrame(rows,columns=schema)
    final=pd.concat([base[schema],add[schema]],ignore_index=True)
    final['isin']=final['isin'].astype(str).str.strip().str.upper()
    unique_isin=int(final['isin'].nunique())
    if len(final)!=1829 or unique_isin!=1829:
        raise RuntimeError(f'Final rows/unique invalid {len(final)}/{unique_isin}')

    forbidden={'LIVE','ORDER','EXECUTE','BROKER','REAL_ORDER','LIVE_ORDER'}
    live=final.get('execution',pd.Series('',index=final.index)).astype(str).str.upper().isin(forbidden).any()
    if live:
        raise RuntimeError('Live execution value detected')

    OUT.parent.mkdir(parents=True,exist_ok=True)
    AUDIT.parent.mkdir(parents=True,exist_ok=True)
    final.to_csv(OUT,sep=';',index=False,encoding='utf-8-sig')
    quality=pd.to_numeric(add['candidate_enrichment_quality_pct'],errors='coerce')
    audit={
        'rows':int(len(final)),
        'unique_isin':unique_isin,
        'columns':int(len(final.columns)),
        'canonical_retained':1429,
        'quarantine_integrated':400,
        'quarantine_remaining_review':88,
        'quarantine_excluded':18,
        'candidate_enrichment_quality_mean_pct':round(float(quality.mean()),2),
        'candidate_quality_ge_60_pct':int((quality>=60).sum()),
        'candidate_quality_ge_80_pct':int((quality>=80).sum()),
        'source_quarantine_run':'31292369240',
        'smart_money_enabled':False,
        'live_order_execution_enabled':False,
        'execution_guard':'NO_LIVE_EXECUTION',
        'passed':bool(len(final)==1829 and unique_isin==1829 and len(final.columns)>=272 and not live),
    }
    AUDIT.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write_xlsx(final,audit)
    print('FINAL_PEA_REFERENCE',audit,flush=True)


if __name__=='__main__':
    main()
