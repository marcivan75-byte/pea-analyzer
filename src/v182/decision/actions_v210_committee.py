from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json, math
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
CONFIG=ROOT/'data/reference/V21.0_ACTIONS_PEA_CONFIG.json'
IN=ROOT/'outputs/V21.0_ACTIONS_PEA_1429_PREPARED.csv'
OUT=ROOT/'outputs/V21.0_ACTIONS_PEA_1429_COMMITTEE.csv'
AUDIT=ROOT/'outputs/audit/V21.0_ACTIONS_COMMITTEE_AUDIT.json'
SUMMARY=ROOT/'outputs/V21.0_ACTIONS_COMMITTEE_SUMMARY.md'

BOOL_FIELDS={'positive_reversal_flag','stoch_bull_cross_flag','stoch_bear_cross_flag','breakout_20d_flag'}
DIRECT_100={'consensus_score_100_v21','earnings_catalyst_score','news_catalyst_score','valuation_discount_score','sentiment_regime_score'}


def _num(df,col):
    return pd.to_numeric(df[col],errors='coerce') if col in df else pd.Series(np.nan,index=df.index,dtype=float)

def _rank(s,higher=True):
    x=pd.to_numeric(s,errors='coerce'); p=x.rank(pct=True,method='average')*100.0
    return p if higher else 100.0-p

def _bool_score(s):
    text=s.astype(str).str.lower(); obs=~text.isin({'nan','none','<na>',''})
    val=text.isin({'true','1','yes','oui'}).astype(float)*100.0
    return val.where(obs)

def _target_score(s,target,slope):
    x=pd.to_numeric(s,errors='coerce'); return (100-(x-target).abs()*slope).clip(0,100).where(x.notna())

def _sector_rank(df,field,higher):
    raw=_num(df,field); global_rank=_rank(raw,higher)
    if field not in set(json.loads(CONFIG.read_text(encoding='utf-8')).get('sector_neutral_metrics',[])): return global_rank
    sector=df.get('sector_v21',pd.Series(index=df.index,dtype=object)).fillna('UNCLASSIFIED').astype(str)
    out=global_rank.copy()
    for _,idx in sector.groupby(sector).groups.items():
        if raw.loc[idx].notna().sum()>=5: out.loc[idx]=_rank(raw.loc[idx],higher)
    return out

def _distribution_score(s):
    x=s.astype(str).str.upper().str.strip(); mp={'DIST':100.0,'ACC_OR_DIST':60.0,'ACC':0.0}
    return x.map(mp)

def _metric_score(df,field,horizon,cfg):
    if field=='distribution_policy': return _distribution_score(df[field])
    if field in BOOL_FIELDS: return _bool_score(df[field])
    if field=='rsi14': return _target_score(_num(df,field),55 if horizon=='CT' else (35 if horizon=='SHORT' else 55),2.5)
    if field=='payout_ratio_v21_pct': return _target_score(_num(df,field),50,1.4)
    if field in DIRECT_100: return _num(df,field).clip(0,100)
    raw=_num(df,field)
    if horizon=='SHORT':
        lower_weak={'perf_1m_pct','perf_3m_pct','perf_6m_pct','relative_strength','macd_hist','fcf_yield_v21','roe_v21_pct','operating_margin_v21_pct','earnings_growth_v21_pct','target_upside_pct_v21','consensus_score_100_v21','consensus_delta_4w','net_upgrades_30d_v21','max_drawdown_1y'}
        higher_weak={'per_forward_v21','pb_v21','debt_to_ebitda_v21','volatility_20d','volatility_60d','beta_v21'}
        if field=='rvol20':
            p=_rank(raw,True); neg=_num(df,'perf_1m_pct').lt(0); return p.where(neg,0.0).where(raw.notna())
        if field in lower_weak: return _sector_rank(df,field,False) if field in cfg.get('sector_neutral_metrics',[]) else _rank(raw,False)
        if field in higher_weak: return _sector_rank(df,field,True) if field in cfg.get('sector_neutral_metrics',[]) else _rank(raw,True)
        return _rank(raw,False)
    higher=field not in set(cfg.get('lower_is_better',[]))
    return _sector_rank(df,field,higher)

def _weighted(df,weights,hz,cfg):
    numerator=pd.Series(0.0,index=df.index); denominator=pd.Series(0.0,index=df.index); total=sum(weights.values())
    scored={}
    for f,w in weights.items():
        s=_metric_score(df,f,hz,cfg); scored[f]=s
        numerator += s.fillna(0)*float(w); denominator += s.notna().astype(float)*float(w)
    score=(numerator/denominator.replace(0,np.nan)).clip(0,100); coverage=(denominator/total).clip(0,1)
    return score,coverage,scored,denominator

def _decision(s,th):
    if pd.isna(s): return 'REJECT'
    if s>=float(th['BUY_CANDIDATE']): return 'BUY_CANDIDATE'
    if s>=float(th['WATCH']): return 'WATCH'
    if s>=float(th['REVIEW']): return 'REVIEW'
    return 'REJECT'

def build(root:Path|None=None)->dict:
    root=root or ROOT; cfg=json.loads((root/CONFIG.relative_to(ROOT)).read_text(encoding='utf-8'))
    df=pd.read_csv(root/IN.relative_to(ROOT),sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
    if len(df)!=1429 or df['isin'].astype(str).nunique()!=1429: raise RuntimeError('V21 committee universe gate')

    # Valuation discount is a distinct sector-aware composite; no target-price input avoids double-counting analyst upside.
    parts=[]
    for field,higher,w in [('per_forward_v21',False,.45),('pb_v21',False,.20),('fcf_yield_v21',True,.35)]:
        s=_sector_rank(df,field,higher); parts.append((s,w))
    n=sum(s.fillna(0)*w for s,w in parts); d=sum(s.notna().astype(float)*w for s,w in parts)
    df['valuation_discount_score']=(n/d.replace(0,np.nan)).clip(0,100)

    # Refresh coalesced analyst revision fields without inventing observations.
    if 'broker_weighted_revision_30d' in df:
        x=_num(df,'broker_weighted_revision_30d'); alt=_num(df,'analyst_momentum_score')
        # analyst_momentum is 0..100; use only when explicit revision metric is absent, but preserve separate semantics by converting around 50.
        df['broker_weighted_revision_30d']=x.where(x.notna(),(alt-50)/5.0)
    
    coverage_floor=float(cfg['coverage']['coverage_penalty_floor'])
    horizon_scores={}; horizon_cov={}; contribution_cols={}
    for hz in ['CT','MT','LT']:
        weights=cfg['horizon_weights'][hz]
        raw,cov,scored,den=_weighted(df,weights,hz,cfg)
        adj=raw*(coverage_floor+(1-coverage_floor)*cov)
        dd=_num(df,'max_drawdown_1y'); gate=cfg['gates']; low=hz.lower()
        threshold=float(gate[f'{low}_max_drawdown_malus_below_pct']); points=float(gate[f'{low}_max_drawdown_malus_points'])
        adj += np.where(dd.lt(threshold),points,0.0)
        if hz=='CT': adj *= _num(df,'action_topdown_multiplier_ct').fillna(1.0)
        if hz=='MT': adj *= _num(df,'action_topdown_multiplier_mt').fillna(1.0)
        adj=adj.clip(0,100)
        df[f'score_{low}_raw']=adj.round(2); df[f'weight_coverage_{low}']=cov.round(3)
        for f,w in weights.items():
            ew=np.where(scored[f].notna() & den.gt(0),float(w)/den,0.0)
            contribution_cols[f'effective_weight_{low}_{f}']=np.round(ew,5)
            contribution_cols[f'contrib_{low}_{f}']=(scored[f].fillna(0)*ew).round(3)
        pct=_rank(adj,True); rw=float(cfg['score_calibration']['raw_weight']); pw=float(cfg['score_calibration']['percentile_weight'])
        final=(rw*adj+pw*pct).clip(0,100)
        df[f'score_{low}']=final.round(2); horizon_scores[low]=final; horizon_cov[low]=cov

    short_raw,short_cov,short_scored,short_den=_weighted(df,cfg['short_weights'],'SHORT',cfg)
    short_raw=short_raw*(coverage_floor+(1-coverage_floor)*short_cov)
    dd=_num(df,'max_drawdown_1y'); g=cfg['gates']
    short_raw += np.where(dd.lt(float(g['short_late_drawdown_below_pct'])),float(g['short_late_malus_points']),0.0)
    short_raw=short_raw.clip(0,100); df['score_short_raw']=short_raw.round(2); df['weight_coverage_short']=short_cov.round(3)
    for f,w in cfg['short_weights'].items():
        ew=np.where(short_scored[f].notna() & short_den.gt(0),float(w)/short_den,0.0)
        contribution_cols[f'effective_weight_short_{f}']=np.round(ew,5); contribution_cols[f'contrib_short_{f}']=(short_scored[f].fillna(0)*ew).round(3)
    if contribution_cols:
        df=pd.concat([df,pd.DataFrame(contribution_cols,index=df.index)],axis=1)
    srw=float(cfg['score_calibration']['short_raw_weight']); df['score_short']=(srw*short_raw+(1-srw)*_rank(short_raw,True)).clip(0,100).round(2)

    identity=_num(df,'v182_ticker_validation_confidence_pct')/100.0
    pea_high=df['pea_confidence'].astype(str).str.upper().str.startswith('HIGH')
    liq=_num(df,'iquidity_percentile'); td_gate=df.get('action_topdown_gate',pd.Series('DATA_REQUIRED',index=df.index)).astype(str)
    sm_conf=_num(df,'action_smart_money_confidence'); sm_gate=df.get('action_smart_money_gate',pd.Series('NONE',index=df.index)).astype(str)
    earnings_gate=df.get('earnings_risk_gate',pd.Series('DATA_NOT_AVAILABLE',index=df.index)).astype(str)
    catalyst=_num(df,'earnings_catalyst_score')
    profit=df.get('profit_warning_flag',pd.Series(False,index=df.index)).astype(str).str.lower().isin({'true','1','yes'})

    reasons_by_hz={}
    for hz in ['ct','mt','lt']:
        score=_num(df,f'score_{hz}'); cov=_num(df,f'weight_coverage_{hz}'); th=cfg['thresholds'][hz.upper()]
        decisions=[]; reasons=[]
        for i in df.index:
            dec=_decision(score.loc[i],th); reason='SCORE'
            if identity.loc[i] < float(cfg['coverage']['identity_min']): dec,reason='REVIEW','IDENTITY_CONFIDENCE'
            elif cfg['coverage'].get('pea_buy_requires_high_confidence',True) and not bool(pea_high.loc[i]) and dec in {'BUY_CANDIDATE','WATCH'}: dec,reason='REVIEW' ,'PEA_ELIGIBILITY_REVIEW_ONLY'
            elif cov.loc[i] < float(cfg['coverage']['min_weight_coverage_watch'][hz.upper()]): dec,reason='REVIEW','DATA_COVERAGE_LOW'
            elif dec=='BUY_CANDIDATE' and cov.loc[i] < float(cfg['coverage']['min_weight_coverage_buy'][hz.upper()]): dec,reason='WATCH','DATA_COVERAGE_BUY_GATE'
            elif dec=='BUY_CANDIDATE' and pd.notna(liq.loc[i]) and liq.loc[i] < float(cfg['gates']['bottom_liquidity_percentile_review']): dec,reason='WATCH','LIQUIDITY_BOTTOM_5PCT'
            if td_gate.loc[i]=='BLOCK_BUY' and dec in {'BUY_CANDIDATE','WATCH'}: dec,reason='REVIEW','TOPDOWN_BLOCK_BUY'
            elif td_gate.loc[i]=='REVIEW_ONLY' and dec=='BUY_CANDIDATE': dec,reason='REVIEW','TOPDOWN_REVIEW_ONLY'
            if dec=='BUY_CANDIDATE' and earnings_gate.loc[i]=='IMMINENT_REVIEW' and not (pd.notna(catalyst.loc[i]) and catalyst.loc[i]>=65): dec,reason='WATCH','EARNINGS_IMMINENT'
            if profit.loc[i] and dec in {'BUY_CANDIDATE','WATCH'}: dec,reason='REVIEW','PROFIT_WARNING'
            if pd.notna(sm_conf.loc[i]) and sm_conf.loc[i]>=float(cfg['smart_money']['min_confidence_for_gate']) and sm_gate.loc[i] in {'REVIEW_BUY','BLOCK_BUY'} and dec=='BUY_CANDIDATE': dec,reason='REVIEW','SMART_MONEY_NEGATIVE_GATE'
            decisions.append(dec); reasons.append(reason)
        df[f'decision_{hz}']=decisions; df[f'decision_reason_{hz}']=reasons
        df[f'rank_{hz}']=score.rank(method='min',ascending=False).astype('Int64')
        reasons_by_hz[hz]=pd.Series(reasons).value_counts().to_dict()

    # Short is an avoidance/risk signal only. Low liquidity makes it non-actionable rather than more attractive.
    st=cfg['thresholds']['SHORT']; sscore=_num(df,'score_short'); scov=_num(df,'weight_coverage_short')
    sdec=[]; sreason=[]
    for i in df.index:
        if pd.isna(sscore.loc[i]) or scov.loc[i] < float(cfg['coverage']['min_weight_coverage_short']): dec,reason='NO_SHORT','DATA_COVERAGE_LOW'
        elif pd.notna(liq.loc[i]) and liq.loc[i] < .10: dec,reason='NO_SHORT','LIQUIDITY_NOT_SHORTABLE'
        elif sscore.loc[i]>=float(st['SHORT_CANDIDATE']): dec,reason='SHORT_CANDIDATE','SCORE'
        elif sscore.loc[i]>=float(st['WATCH_SHORT']): dec,reason='WATCH_SHORT','SCORE'
        else: dec,reason='NO_SHORT','SCORE'
        sdec.append(dec); sreason.append(reason)
    df['decision_short']=sdec; df['decision_reason_short']=sreason; df['rank_short']=sscore.rank(method='min',ascending=False).astype('Int64')

    for hz in ['ct','mt','lt']:
        lim=int(cfg['selection_limits'][hz.upper()]); df[f'selection_{hz}']=(pd.to_numeric(df[f'rank_{hz}'],errors='coerce')<=lim)&df[f'decision_{hz}'].isin(['BUY_CANDIDATE','WATCH'])
    df['selection_short']=(pd.to_numeric(df['rank_short'],errors='coerce')<=int(cfg['selection_limits']['SHORT']))&df['decision_short'].isin(['SHORT_CANDIDATE','WATCH_SHORT'])

    last=_num(df,'last_close'); atr=_num(df,'atr14'); target=_num(df,'target_mean_v21'); inval=_num(df,'invalidation_level')
    reliable=identity.ge(float(cfg['coverage']['identity_min'])) & last.gt(0)
    df['T1_entry_low']=np.where(reliable&atr.notna(),(last-.35*atr).clip(lower=0),np.nan)
    df['T1_entry_high']=np.where(reliable&atr.notna(),last+.10*atr,np.nan)
    df['T1_target']=np.where(reliable&target.notna(),target,np.nan)
    df['T1_invalidation']=np.where(reliable&inval.notna(),inval,np.where(reliable&atr.notna(),(last-1.6*atr).clip(lower=0),np.nan))
    df['T0']=np.where(df['decision_ct'].eq('BUY_CANDIDATE'),'PREPARE',np.where(df['decision_ct'].eq('WATCH'),'WATCH','NO_ACTION'))
    df['T1_1_4w']=np.where(df['decision_ct'].eq('BUY_CANDIDATE'),'ENTRY_IF_ZONE_VALID','MONITOR')
    df['T2_1_3m']=np.where(df['score_mt'].ge(68),'HOLD_OR_ADD_IF_THESIS_VALID','REASSESS')
    df['T3_3_6m']=np.where(df['score_mt'].ge(65),'HOLD','REVIEW')
    df['T4_6_12m']=np.where(df['score_lt'].ge(65),'HOLD','REVIEW')
    df['T5_12_24m']=np.where(df['score_lt'].ge(70),'CORE_CANDIDATE','REASSESS')
    df['execution']='RESEARCH_ONLY'; df['v210_version']=cfg['version']

    outp=root/OUT.relative_to(ROOT); outp.parent.mkdir(parents=True,exist_ok=True); df.to_csv(outp,sep=';',index=False,encoding='utf-8-sig')
    decisions={hz:df[f'decision_{hz}'].value_counts().to_dict() for hz in ['ct','mt','lt','short']}
    audit={'passed':True,'version':cfg['version'],'rows':len(df),'columns':len(df.columns),'unique_isin':int(df['isin'].nunique()),'decisions':decisions,'selection_counts':{hz:int(df[f'selection_{hz}'].sum()) for hz in ['ct','mt','lt','short']},'mean_weight_coverage':{hz:round(float(pd.to_numeric(df[f'weight_coverage_{hz}'],errors='coerce').mean()),4) for hz in ['ct','mt','lt','short']},'pea_review_only_rows':int((~pea_high).sum()),'topdown_gates':td_gate.value_counts().to_dict(),'smart_money_positive_score_boost_allowed':False,'smart_money_negative_gate_rows':int((sm_gate!='NONE').sum()),'earnings_imminent_rows':int((earnings_gate=='IMMINENT_REVIEW').sum()),'reason_counts':reasons_by_hz,'execution':'RESEARCH_ONLY','generated_at_utc':datetime.now(timezone.utc).isoformat()}
    ap=root/AUDIT.relative_to(ROOT); ap.parent.mkdir(parents=True,exist_ok=True); ap.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# V21.0 PEA Actions 1429 — Comité','',f"- Universe: {len(df)} canonical PEA Actions",f"- Columns: {len(df.columns)}",f"- Mean weighted coverage: {audit['mean_weight_coverage']}",f"- Decisions: {decisions}",f"- Selections: {audit['selection_counts']}",'- Smart Money positive contribution: OFF; negative high-confidence gate only','- Execution: RESEARCH_ONLY']
    (root/SUMMARY.relative_to(ROOT)).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('V21_ACTIONS_COMMITTEE_OK',{'decisions':decisions,'selection':audit['selection_counts'],'coverage':audit['mean_weight_coverage']})
    return audit

if __name__=='__main__': build()
