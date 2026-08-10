from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import json,math
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3]; CONFIG=ROOT/'data/reference/V21.2_TCT_EXPLOSIF_OPT_CONFIG.json'
def _num(df,*cols):
    out=pd.Series(np.nan,index=df.index,dtype=float)
    for col in cols:
        if col in df.columns: out=out.where(out.notna(),pd.to_numeric(df[col],errors='coerce'))
    return out
def _truth(s):
    t=s.astype(str).str.strip().str.lower(); obs=~t.isin({'','nan','none','<na>','null'}); return t.isin({'true','1','yes','oui','y'}).astype(float).mul(100).where(obs)
def _rank(s,higher=True):
    x=pd.to_numeric(s,errors='coerce'); p=x.rank(pct=True,method='average')*100; return p if higher else 100-p
def _piece(x,xp,fp):
    raw=pd.to_numeric(x,errors='coerce'); arr=np.interp(raw.fillna(xp[0]).to_numpy(float),xp,fp,left=fp[0],right=fp[-1]); return pd.Series(arr,index=x.index).where(raw.notna()).clip(0,100)
def _weighted(parts):
    idx=next(iter(parts.values()))[0].index; num=pd.Series(0.,index=idx); den=pd.Series(0.,index=idx); total=sum(w for _,w in parts.values())
    for s,w in parts.values():
        x=pd.to_numeric(s,errors='coerce').clip(0,100); num+=x.fillna(0)*w; den+=x.notna().astype(float)*w
    return (num/den.replace(0,np.nan)).clip(0,100),(den/total).clip(0,1)
def _rsi(s):
    x=pd.to_numeric(s,errors='coerce'); out=pd.Series(np.nan,index=x.index); out.loc[x.between(50,65)]=100; out.loc[x<50]=(100-(50-x[x<50])*4).clip(0,100); out.loc[x>65]=(100-(x[x>65]-65)*5).clip(0,100); return out
def _prov(df,field,cfg):
    mult=pd.Series(1.,index=df.index); cc=f'v211_{field}_confidence'; fc=f'v211_{field}_freshness'
    if cc in df.columns:
        c=pd.to_numeric(df[cc],errors='coerce'); mult=mult.where(c.isna(),c.clip(float(cfg['source_policy']['minimum_confidence_multiplier']),1.0))
    if fc in df.columns:
        stale=df[fc].astype(str).str.upper().eq('STALE_WARNING'); mult=mult.where(~stale,mult*float(cfg['source_policy']['stale_warning_multiplier']))
    return mult
def _direct(df,cfg,*fields,cap=None):
    out=pd.Series(np.nan,index=df.index,dtype=float); chosen=pd.Series('',index=df.index,dtype=object)
    for f in fields:
        if f not in df.columns: continue
        x=pd.to_numeric(df[f],errors='coerce'); take=out.isna()&x.notna(); out=out.where(~take,x); chosen=chosen.where(~take,f)
    for f in fields:
        m=chosen.eq(f)
        if m.any(): out.loc[m]=out.loc[m]*_prov(df,f,cfg).loc[m]
    if cap is not None: out=out.clip(upper=cap)
    return out.clip(0,100)
def _derived(df):
    out=df.copy(); pe=_num(out,'per_forward_v21','per_forward'); sector=out.get('sector_v21',pd.Series('UNCLASSIFIED',index=out.index)).fillna('UNCLASSIFIED').astype(str); valid=pe.where(pe>0); med=valid.groupby(sector).transform('median'); gm=float(valid.median()) if valid.notna().any() else math.nan; med=med.fillna(gm); out['per_vs_sector_pct_v212']=((valid/med)-1)*100; growth=_num(out,'earnings_growth_v21_pct'); out['peg_v212']=(valid/growth).where((valid>0)&(growth>0)); ev=_num(out,'enterprise_value_v21'); ebitda=_num(out,'ebitda_v21'); out['ev_to_ebitda_v212']=(ev/ebitda).where((ev>0)&(ebitda>0)); vol=_num(out,'volume'); vavg=_num(out,'volume_avg_20d'); out['rvol_v212']=_num(out,'rvol20_v212','rvol20','volume_acceleration_20d'); out['rvol_v212']=out['rvol_v212'].where(out['rvol_v212'].notna(),(vol/vavg).where(vavg>0)); out['volume_trend_v212']=_num(out,'volume_trend_5_20_v212','volume_trend_5_20','volume_acceleration_20d'); out['gap_up_pct_v212']=_num(out,'gap_up_pct_v212','gap_up_pct_v211','gap_up_pct','open_gap_pct'); gd=_num(out,'gdelt_catalyst_discovery_score'); out['tct_gdelt_attention_flag']=gd.ge(65).where(gd.notna()); return out
def compute_scores(df,cfg):
    out=_derived(df); tw=cfg['technical_weights']; breakout=_truth(out.get('breakout_20d_flag_v212',out.get('breakout_20d_flag',pd.Series(index=out.index,dtype=object)))); dist=_num(out,'breakout_distance_pct_v212'); breakout=breakout.where(breakout.notna(),_piece(dist,[-10,-5,-2,0,3],[0,20,60,100,100])); align=_truth(out.get('trend_alignment_flag_v212',out.get('ma_alignment_flag_v211',pd.Series(index=out.index,dtype=object)))); macd,_=_weighted({'base':(_rank(_num(out,'macd_hist_v212','macd_hist')),0.6),'acc':(_rank(_num(out,'macd_hist_delta3_v212')),0.4)}); bb=_piece(_num(out,'bb_width_change5_pct_v212'),[-40,-10,0,15,40,80],[10,30,50,75,100,80]); atr=_piece(_num(out,'atr_expansion_ratio_v212'),[.5,.8,1,1.2,1.5,2.5],[20,40,55,75,100,80]); rev,_=_weighted({'reversal':(_truth(out.get('positive_reversal_flag',pd.Series(index=out.index,dtype=object))),.55),'stoch':(_truth(out.get('stoch_bull_cross_flag_v212',out.get('stoch_bull_cross_flag',pd.Series(index=out.index,dtype=object)))),.45)}); technical,tcov=_weighted({'breakout':(breakout,tw['breakout_strength']),'align':(align,tw['trend_alignment']),'macd':(macd,tw['macd_acceleration']),'rsi':(_rsi(_num(out,'rsi14_v212','rsi14')),tw['rsi_sweet_zone']),'bb':(bb,tw['bollinger_expansion']),'atr':(atr,tw['atr_expansion']),'rs':(_rank(_num(out,'relative_strength')),tw['relative_strength']),'rev':(rev,tw['reversal_stoch'])})
    cw=cfg['catalyst_weights']; rumorcap=float(cfg['gates']['rumor_only_event_cap']); confirmed=_direct(out,cfg,'mna_confirmed_score','mna_event_score'); rumor=_direct(out,cfg,'mna_rumor_score',cap=rumorcap); mna=confirmed.where(confirmed.notna(),rumor); primary_fields=['mna_confirmed_score','regulated_event_score','regulatory_catalyst_score','fda_catalyst_score','ema_catalyst_score','issuer_event_score','primary_news_catalyst_score','major_contract_confirmed_score','guidance_confirmed_score']; primary_confirm=pd.Series(False,index=out.index)
    for pf in primary_fields:
        if pf in out.columns: primary_confirm|=pd.to_numeric(out[pf],errors='coerce').fillna(0).gt(0)
    for pf in ['primary_event_confirmed_flag','news_primary_confirmed_flag','issuer_event_confirmed_flag','regulated_event_confirmed_flag']:
        if pf in out.columns: primary_confirm|=_truth(out[pf]).fillna(0).ge(100)
    out['tct_primary_event_confirmed_v212']=primary_confirm; catalyst,ccov=_weighted({'mna':(mna,cw['mna']),'earnings':(_direct(out,cfg,'earnings_catalyst_score',cap=70),cw['earnings']),'guidance':(_direct(out,cfg,'guidance_revision_score'),cw['guidance']),'contract':(_direct(out,cfg,'major_contract_score','contract_catalyst_score'),cw['major_contract']),'reg':(_direct(out,cfg,'regulatory_catalyst_score'),cw['regulatory']),'fda':(_direct(out,cfg,'fda_catalyst_score','ema_catalyst_score'),cw['fda_ema'])})
    vw=cfg['volume_weights']; rvol=_num(out,'rvol_v212'); vtrend=_num(out,'volume_trend_v212'); short=_num(out,'short_percent_float_pct'); amf=_num(out,'amf_public_short_pct','amf_short_pct','public_short_pct'); dcover=_num(out,'short_ratio'); gap=_num(out,'gap_up_pct_v212'); volume,vcov=_weighted({'rvol':(_piece(rvol,[0,.75,1,1.5,2,3,5],[0,10,25,60,85,100,100]),vw['relative_volume']),'vtrend':(_piece(vtrend,[.5,.8,1,1.15,1.3,1.6,2],[0,15,35,60,80,100,100]),vw['volume_trend']),'short':(_piece(short,[0,3,5,10,15,25],[0,15,35,65,90,100]),vw['short_float']),'amf':(_piece(amf,[0,.5,1,2,4],[0,30,50,75,100]),vw['amf_public_short']),'dc':(_piece(dcover,[0,1,3,5,8,12],[0,20,45,70,90,100]),vw['days_to_cover']),'gap':(_piece(gap,[-5,0,1,2,5,10,15],[0,20,45,75,100,75,35]),vw['gap_confirmation'])})
    primary=_direct(out,cfg,'primary_news_catalyst_score','issuer_event_score','regulated_event_score'); secondary=_direct(out,cfg,'news_catalyst_score',cap=float(cfg['gates']['secondary_news_cap'])); sector_news=_direct(out,cfg,'sector_news_score',cap=float(cfg['gates']['sector_news_cap'])); news,ncov=_weighted({'primary':(primary,.50),'secondary':(secondary,.25),'sector':(sector_news,.15),'sector_mom':(_rank(_num(out,'sector_perf_5d_pct','sector_momentum_5d_pct')),.10)}); aw=cfg['analyst_weights']; analyst,acov=_weighted({'broker':(_rank(_num(out,'broker_weighted_revision_30d')),aw['broker_revision']),'up':(_rank(_num(out,'net_upgrades_30d_v21')),aw['net_upgrades']),'delta':(_rank(_num(out,'consensus_delta_4w')),aw['consensus_delta']),'mom':(_direct(out,cfg,'analyst_momentum_score'),aw['analyst_momentum'])})
    vd=_direct(out,cfg,'valuation_discount_score')
    if vd.notna().any(): valuation=vd; valcov=vd.notna().astype(float)
    else: valuation,valcov=_weighted({'per':(_piece(-_num(out,'per_vs_sector_pct_v212'),[-50,0,20,40,70],[0,35,65,85,100]),.40),'peg':(_piece(_num(out,'peg_v212'),[.1,.5,.8,1.2,2,3],[90,100,100,70,30,0]),.25),'fcf':(_piece(_num(out,'fcf_yield_v21'),[-10,0,2,5,8,15],[0,20,45,70,100,100]),.25),'ev':(_piece(_num(out,'ev_to_ebitda_v212'),[3,6,10,15,25,40],[100,100,90,60,20,0]),.10)})
    liq=_num(out,'liquidity_percentile','iquidity_percentile'); liq01=liq.where(liq<=1,liq/100); risk,rcov=_weighted({'liq':((liq01*100).clip(0,100),.45),'dd':(_piece(_num(out,'max_drawdown_1y'),[-70,-50,-45,-30,-20,-10,0],[0,10,20,50,70,90,100]),.30),'vol':(_piece(_num(out,'volatility_20d','volatility_1y_pct'),[5,15,25,45,60,90,130],[20,50,80,100,80,35,0]),.25)}); corporate=_direct(out,cfg,'buyback_score'); corporate=corporate if corporate.notna().any() or 'buyback_signal' not in out.columns else _truth(out['buyback_signal']); corp_cov=corporate.notna().astype(float); macro,mcov=_weighted({'td':(_direct(out,cfg,'action_topdown_score'),.80),'sent':(_direct(out,cfg,'sentiment_regime_score'),.20)}); insider,_=_weighted({'net':(_rank(_num(out,'insider_net_buy_90d')),.5),'legacy':(_direct(out,cfg,'insider_score'),.3),'cluster':(_truth(out.get('insider_cluster_flag',pd.Series(index=out.index,dtype=object))),.2)}); out['tct_insider_shadow_score_v212']=insider.round(2)
    pillars={'technical_impulse':(technical,tcov),'catalyst_event':(catalyst,ccov),'volume_flow_squeeze':(volume,vcov),'news_sector':(news,ncov),'analyst_revision':(analyst,acov),'risk_liquidity':(risk,rcov),'valuation_relative':(valuation,valcov),'corporate_support':(corporate,corp_cov),'macro_rotation':(macro,mcov)}
    for n,(s,c) in pillars.items(): out[f'tct_{n}_score_v212']=s.round(2); out[f'tct_{n}_coverage_v212']=c.round(3)
    pw=cfg['pillar_weights']; num=pd.Series(0.,index=out.index); covnum=pd.Series(0.,index=out.index)
    for n,w in pw.items(): s,c=pillars[n]; num+=s.fillna(0)*w; covnum+=c.fillna(0)*w
    raw=num/sum(pw.values()); alpha_cov=covnum/sum(pw.values()); volume_market_cov=(rvol.notna().astype(float)*.65+vtrend.notna().astype(float)*.35).clip(0,1); critical_cov=(tcov.fillna(0)*.55+volume_market_cov*.30+rcov.fillna(0)*.15).clip(0,1); out['tct_volume_market_coverage_v212']=volume_market_cov.round(3); out['tct_alpha_coverage_v212']=alpha_cov.round(3); sy=cfg['synergy']; bonus=pd.Series(0.,index=out.index); bonus+=np.where((technical>=sy['technical_volume_min'])&(volume>=sy['technical_volume_min']),sy['technical_volume_bonus'],0); bonus+=np.where((catalyst>=sy['catalyst_technical_min'])&(technical>=sy['catalyst_technical_min']),sy['catalyst_technical_bonus'],0); bonus+=np.where((catalyst>=sy['catalyst_volume_min'])&(volume>=sy['catalyst_volume_min']),sy['catalyst_volume_bonus'],0); squeeze=short.ge(sy['short_squeeze_short_pct_min'])&rvol.ge(sy['short_squeeze_rvol_min'])&vtrend.ge(sy['short_squeeze_volume_trend_min'])&breakout.ge(100); bonus+=np.where(squeeze,sy['short_squeeze_bonus'],0); bonus=bonus.clip(upper=sy['max_total_bonus']); penalty=pd.Series(0.,index=out.index); tg=out.get('action_topdown_gate',pd.Series('',index=out.index)).astype(str).str.upper(); penalty+=np.where(tg.eq('BLOCK_BUY'),cfg['gates']['topdown_block_penalty'],0); penalty+=np.where(tg.eq('REVIEW_ONLY'),cfg['gates']['topdown_review_penalty'],0); penalty+=np.where(gap.gt(cfg['gates']['extreme_gap_penalty_above_pct']),cfg['gates']['extreme_gap_penalty'],0); penalty+=np.where(_num(out,'max_drawdown_1y').lt(cfg['gates']['max_drawdown_review_below_pct']),-5,0); out['tct_score_raw_v212']=raw.round(2); out['tct_score_coverage_v212']=critical_cov.round(3); out['tct_synergy_bonus_v212']=bonus.round(2); out['tct_risk_penalty_v212']=penalty.round(2); out['tct_score_v212']=(raw+bonus+penalty).clip(0,100).round(2); out['tct_short_squeeze_pattern_v212']=squeeze
    if 'tct_probability_20d_calibrated_v212' not in out: out['tct_probability_20d_calibrated_v212']=np.nan
    return out
def _cap(dec,cap):
    order={'REJECT_TCT':0,'SCAN_TCT_EXPLOSIF':1,'SATELLITE_TCT_EXPLOSIF':2,'COEUR_TCT_EXPLOSIF':3}; return dec if order[dec]<=order[cap] else cap
def apply_decisions(df,cfg):
    out=df.copy(); th=cfg['thresholds']; cv=cfg['coverage']; g=cfg['gates']; score=_num(out,'tct_score_v212'); gcov=_num(out,'tct_score_coverage_v212'); tcov=_num(out,'tct_technical_impulse_coverage_v212'); vcov=_num(out,'tct_volume_market_coverage_v212'); ccov=_num(out,'tct_catalyst_event_coverage_v212'); tech=_num(out,'tct_technical_impulse_score_v212'); vol=_num(out,'tct_volume_flow_squeeze_score_v212'); cat=_num(out,'tct_catalyst_event_score_v212'); ident=_num(out,'v182_ticker_validation_confidence_pct'); ident=ident.where(ident<=1,ident/100); liq=_num(out,'liquidity_percentile','iquidity_percentile'); liq=liq.where(liq<=1,liq/100); prob=_num(out,'tct_probability_20d_calibrated_v212'); gap=_num(out,'gap_up_pct_v212'); pea=out.get('pea_confidence',pd.Series('',index=out.index)).astype(str).str.upper().str.startswith('HIGH'); warn=out.get('profit_warning_flag',pd.Series(False,index=out.index)).astype(str).str.lower().isin({'true','1','yes','oui'}); grade=out.get('coverage_grade_v21',pd.Series('',index=out.index)).astype(str).str.upper(); decisions=[]; reasons=[]
    for i in out.index:
        s=score.loc[i]; dec='REJECT_TCT'; reason='SCORE_BELOW_SCAN'
        if pd.notna(s) and s>=th['COEUR_TCT_EXPLOSIF']: dec='COEUR_TCT_EXPLOSIF'; reason='SCORE'
        elif pd.notna(s) and s>=th['SATELLITE_TCT_EXPLOSIF']: dec='SATELLITE_TCT_EXPLOSIF'; reason='SCORE'
        elif pd.notna(s) and s>=th['SCAN_TCT_EXPLOSIF']: dec='SCAN_TCT_EXPLOSIF'; reason='SCORE'
        if pd.isna(gcov.loc[i]) or gcov.loc[i]<cv['min_global']: dec='REJECT_TCT'; reason='GLOBAL_COVERAGE_LOW'
        if pd.isna(ident.loc[i]) or ident.loc[i]<cv['identity_min_any']: dec='REJECT_TCT'; reason='IDENTITY_CONFIDENCE'
        if 'pea_confidence' in out.columns and not bool(pea.loc[i]): dec='REJECT_TCT'; reason='PEA_NOT_HIGH'
        if bool(warn.loc[i]) and g['profit_warning_policy']=='REJECT': dec='REJECT_TCT'; reason='PROFIT_WARNING'
        if pd.notna(liq.loc[i]) and liq.loc[i]<g['min_liquidity_percentile_any']: dec='REJECT_TCT'; reason='LIQUIDITY_BOTTOM_5PCT'
        if dec=='COEUR_TCT_EXPLOSIF':
            if gcov.loc[i]<cv['min_core']: dec='SATELLITE_TCT_EXPLOSIF'; reason='CORE_GLOBAL_COVERAGE'
            elif pd.isna(tcov.loc[i]) or tcov.loc[i]<cv['min_technical_core']: dec='SATELLITE_TCT_EXPLOSIF'; reason='CORE_TECHNICAL_COVERAGE'
            elif pd.isna(vcov.loc[i]) or vcov.loc[i]<cv['min_volume_core']: dec='SATELLITE_TCT_EXPLOSIF'; reason='CORE_VOLUME_COVERAGE'
            elif ident.loc[i]<cv['identity_min_core']: dec='SATELLITE_TCT_EXPLOSIF'; reason='CORE_IDENTITY_CONFIDENCE'
            elif pd.isna(liq.loc[i]) or liq.loc[i]<g['min_liquidity_percentile_core']: dec='SATELLITE_TCT_EXPLOSIF'; reason='CORE_LIQUIDITY'
            elif pd.notna(gap.loc[i]) and gap.loc[i]>g['extreme_gap_core_block_above_pct']: dec='SATELLITE_TCT_EXPLOSIF'; reason='CORE_GAP_CHASE_BLOCK'
            else:
                event_ok=pd.notna(cat.loc[i]) and cat.loc[i]>=70 and pd.notna(ccov.loc[i]) and ccov.loc[i]>=cv['min_catalyst_core_if_used'] and bool(out.loc[i,'tct_primary_event_confirmed_v212']); flow_ok=pd.notna(tech.loc[i]) and pd.notna(vol.loc[i]) and tech.loc[i]>=78 and vol.loc[i]>=78
                if not(event_ok or flow_ok): dec='SATELLITE_TCT_EXPLOSIF'; reason='CORE_CONFIRMATION_MISSING'
                elif pd.notna(prob.loc[i]) and prob.loc[i]<cfg['objective']['probability_target']: dec='SATELLITE_TCT_EXPLOSIF'; reason='CALIBRATED_PROBABILITY_BELOW_TARGET'
        if dec=='SATELLITE_TCT_EXPLOSIF' and (gcov.loc[i]<cv['min_satellite'] or pd.isna(tcov.loc[i]) or tcov.loc[i]<cv['min_technical_satellite'] or pd.isna(vcov.loc[i]) or vcov.loc[i]<cv['min_volume_satellite']): dec='SCAN_TCT_EXPLOSIF'; reason='SATELLITE_KEY_COVERAGE'
        if grade.loc[i].startswith('D_'): dec=_cap(dec,g['coverage_grade_d_max_decision']); reason='BASE_COVERAGE_GRADE_D'
        elif grade.loc[i].startswith('C_') and dec=='COEUR_TCT_EXPLOSIF': dec=g['coverage_grade_c_max_decision']; reason='BASE_COVERAGE_GRADE_C'
        decisions.append(dec); reasons.append(reason)
    out['tct_decision_v212']=decisions; out['tct_decision_reason_v212']=reasons; action=out['tct_decision_v212'].isin({'COEUR_TCT_EXPLOSIF','SATELLITE_TCT_EXPLOSIF','SCAN_TCT_EXPLOSIF'}); out['tct_rank_v212']=score.where(action).rank(method='min',ascending=False).astype('Int64'); out['tct_top20_v212']=pd.to_numeric(out['tct_rank_v212'],errors='coerce').le(20)&action; return out
def _resolve(root,cfg):
    for rel in cfg['input_candidates']:
        p=root/rel
        if p.exists(): return p
    raise FileNotFoundError('No V21/V21.1 reference available for V21.2 TCT')
def build(root=None):
    root=root or ROOT; cfg=json.loads((root/CONFIG.relative_to(ROOT)).read_text()); source=_resolve(root,cfg); df=pd.read_csv(source,sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
    if len(df)!=cfg['canonical_universe_size'] or df['isin'].astype(str).nunique()!=cfg['canonical_universe_size']: raise RuntimeError('V21.2 TCT canonical universe gate')
    out=apply_decisions(compute_scores(df,cfg),cfg); full=root/cfg['outputs']['full']; top=root/cfg['outputs']['top20']; auditp=root/cfg['outputs']['audit']; full.parent.mkdir(parents=True,exist_ok=True); auditp.parent.mkdir(parents=True,exist_ok=True); out.to_csv(full,sep=';',index=False,encoding='utf-8-sig'); out[out['tct_top20_v212']].sort_values('tct_rank_v212').to_csv(top,sep=';',index=False,encoding='utf-8-sig'); audit={'passed':True,'version':cfg['version'],'source':str(source.relative_to(root)),'rows':len(out),'unique_isin':out['isin'].astype(str).nunique(),'weights_sum':round(sum(cfg['pillar_weights'].values()),8),'decision_counts':out['tct_decision_v212'].value_counts().to_dict(),'top20_rows':int(out['tct_top20_v212'].sum()),'mean_coverage':round(float(_num(out,'tct_score_coverage_v212').mean()),4),'mean_alpha_coverage':round(float(_num(out,'tct_alpha_coverage_v212').mean()),4),'gdelt_direct_weight':cfg['source_policy']['gdelt_direct_score_weight'],'insider_positive_weight_active':cfg['governance']['insider_positive_weight_active'],'no_interpillar_missing_weight_redistribution':cfg['governance']['no_interpillar_missing_weight_redistribution'],'optimization_status':cfg['optimization_status'],'generated_at_utc':datetime.now(timezone.utc).isoformat()}; auditp.write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str)+'\n'); print('V21_2_TCT_EXPLOSIF_OK',audit); return audit
if __name__=='__main__': build()
