from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math
import os
import time
import numpy as np
import pandas as pd

from v182.sources.finnhub_consensus import _SCORE_WEIGHTS,_cache_fresh,_get_json,_label_from_score,_load_symbol_cache,_pick_lookup_result,_save_symbol_cache

ROOT=Path(__file__).resolve().parents[3]
CONFIG=ROOT/'data/reference/V21.0_ACTIONS_PEA_CONFIG.json'
TARGET=ROOT/'outputs/V21.0_ACTIONS_PEA_1829_PREPARED.csv'
AUDIT=ROOT/'outputs/audit/V21.0_ACTIONS_FINNHUB_BACKFILL.json'
CACHE=ROOT/'outputs/cache/V21.0_FINNHUB_SYMBOL_MAP.csv'
BROKER_WEIGHTS=ROOT/'config/V18.2_BROKER_WEIGHTS.csv'
MAX_SYMBOLS=int(os.getenv('FINNHUB_MAX_SYMBOLS_PER_RUN','750') or '750')
METRIC_MAX=int(os.getenv('FINNHUB_METRIC_MAX_SYMBOLS_PER_RUN','450') or '450')
DELAY=float(os.getenv('FINNHUB_DELAY_SECONDS','1.05') or '1.05')


def _f(value):
    try:
        x=float(value); return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None

def _missing_value(value):
    if value is None: return True
    try:
        if pd.isna(value): return True
    except Exception: pass
    return str(value).strip().lower() in {'','nan','none','null','n/a','<na>'}

def _ensure_object(df,field):
    if field not in df.columns: df[field]=pd.Series(pd.NA,index=df.index,dtype='object')
    else: df[field]=df[field].astype('object')

def _fill(df,i,field,value):
    if value is None:return False
    if field not in df.columns:df[field]=pd.Series(pd.NA,index=df.index,dtype='object')
    if _missing_value(df.at[i,field]):df.at[i,field]=value;return True
    return False

def _score5(rec):
    counts={k:max(0,int(rec.get(k) or 0)) for k in _SCORE_WEIGHTS}; total=sum(counts.values())
    return (float(sum(counts[k]*_SCORE_WEIGHTS[k] for k in counts)/total),total) if total>0 else (None,0)

def _score100(score5):return None if score5 is None else round((score5-1.0)/4.0*100.0,4)

def _metric(metric,names):
    for name in names:
        val=_f(metric.get(name))
        if val is not None:return val
    return None

def _broker_weight_map():
    if not BROKER_WEIGHTS.exists():return {}
    try:d=pd.read_csv(BROKER_WEIGHTS,sep=';',dtype=str,encoding='utf-8-sig')
    except Exception:return {}
    if not {'broker','weight'}.issubset(d.columns):return {}
    out={}
    for _,r in d.iterrows():
        name=str(r.get('broker') or '').strip().casefold();val=_f(r.get('weight'))
        if name and val and val>0:out[name]=val
    return out

def _grade_rank(text):
    x=str(text or '').upper().replace('-',' ').replace('_',' ')
    if any(k in x for k in ['STRONG BUY','OUTPERFORM','OVERWEIGHT','BUY']):return 4
    if any(k in x for k in ['HOLD','NEUTRAL','MARKET PERFORM','EQUAL WEIGHT']):return 3
    if any(k in x for k in ['UNDERPERFORM','UNDERWEIGHT','REDUCE']):return 2
    if 'SELL' in x:return 1
    return None

def _resolved_symbol(session,cache,*,isin,yahoo_ticker,name,token):
    key=isin or f'TICKER:{yahoo_ticker}';cached=cache.get(key,{});same=cached and str(cached.get('yahoo_ticker') or '').upper()==yahoo_ticker.upper()
    if same and _cache_fresh(cached,90,14):
        status=str(cached.get('status') or '').upper()
        if status=='RESOLVED' and cached.get('finnhub_symbol'):return str(cached['finnhub_symbol']),'CACHE'
        if status=='UNRESOLVED':return None,'UNRESOLVED_CACHED'
    lookup=_get_json(session,'/search',{'q':isin or yahoo_ticker or name,'token':token},max_retries=2)
    best=_pick_lookup_result((lookup or {}).get('result',[]),yahoo_ticker,name=name,queried_by_isin=bool(isin),min_score=8);now=datetime.now(timezone.utc).isoformat()
    if not best:
        cache[key]={'isin':isin,'yahoo_ticker':yahoo_ticker,'finnhub_symbol':'','status':'UNRESOLVED','updated_at':now};return None,'UNRESOLVED'
    symbol=str(best.get('symbol') or '').strip();cache[key]={'isin':isin,'yahoo_ticker':yahoo_ticker,'finnhub_symbol':symbol,'status':'RESOLVED','updated_at':now};return symbol,'SEARCH'


def main():
    cfg=json.loads(CONFIG.read_text(encoding='utf-8'));expected=int(cfg['canonical_universe_size']);token=str(os.getenv('FINNHUB_API_KEY') or '').strip()
    df=pd.read_csv(TARGET,sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
    if len(df)!=expected or df['isin'].astype(str).nunique()!=expected:raise RuntimeError(f'Finnhub backfill requires canonical {expected} Actions universe')
    for field in ['consensus_label_v21','consensus_source_v21','target_source_v21','analyst_coverage_status_v21','analyst_coverage_source_v21','finnhub_symbol_v21','finnhub_checked_at_utc']:_ensure_object(df,field)
    tracked=['market_cap_v21','pb_v21','roe_v21_pct','roa_v21_pct','operating_margin_v21_pct','net_margin_v21_pct','revenue_growth_v21_pct','earnings_growth_v21_pct','debt_to_equity_v21','current_ratio_v21','dividend_yield_v21_pct','beta_v21','target_mean_v21','n_analysts_v21','consensus_score_100_v21','consensus_delta_4w','net_upgrades_30d_v21','broker_weighted_revision_30d','next_earnings_date']
    before={f:round(float(df[f].notna().mean()*100),2) if f in df else 0.0 for f in tracked}
    audit={'passed':True,'status':'ACTIVE' if token else 'SKIPPED_NO_KEY','rows':len(df),'expected_rows':expected,'max_symbols':MAX_SYMBOLS,'metric_max':METRIC_MAX,'attempted':0,'resolved':0,'unresolved':0,'recommendation_observed':0,'no_analyst_coverage_confirmed':0,'target_observed':0,'metric_observed':0,'filled_cells':0,'errors':[],'generated_at_utc':datetime.now(timezone.utc).isoformat()}
    if not token:
        AUDIT.parent.mkdir(parents=True,exist_ok=True);AUDIT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8');print('V21_ACTIONS_FINNHUB_BACKFILL_SKIPPED_NO_KEY');return
    needs=pd.Series(False,index=df.index)
    for f in ['consensus_score_100_v21','target_mean_v21','market_cap_v21','pb_v21','roe_v21_pct','revenue_growth_v21_pct','earnings_growth_v21_pct']:
        needs |= df[f].isna() if f in df else True
    score=pd.to_numeric(df.get('v210_enrichment_priority_score'),errors='coerce').fillna(-1);candidates=list(df.index[needs]);candidates.sort(key=lambda i:float(score.loc[i]),reverse=True);candidates=candidates[:MAX_SYMBOLS]
    import requests
    session=requests.Session();cache=_load_symbol_cache(CACHE);symbol_to_idx={};metric_count=0;checked_at=datetime.now(timezone.utc).isoformat()
    metric_mapping={'market_cap_v21':['marketCapitalization','marketCapitalizationTTM'],'per_ttm_v21':['peTTM','peBasicExclExtraTTM','peNormalizedAnnual'],'pb_v21':['pbTTM','pbQuarterly','pbAnnual'],'roe_v21_pct':['roeTTM','roeRfy','roeAnnual'],'roa_v21_pct':['roaTTM','roaRfy','roaAnnual'],'operating_margin_v21_pct':['operatingMarginTTM','operatingMarginAnnual'],'net_margin_v21_pct':['netProfitMarginTTM','netProfitMarginAnnual'],'revenue_growth_v21_pct':['revenueGrowthTTMYoy','revenueGrowthQuarterlyYoy','revenueGrowth5Y'],'earnings_growth_v21_pct':['epsGrowthTTMYoy','epsGrowthQuarterlyYoy','epsGrowth5Y'],'debt_to_equity_v21':['totalDebt/totalEquityQuarterly','totalDebt/totalEquityAnnual'],'debt_to_ebitda_v21':['totalDebt/ebitdaTTM','netDebt/ebitdaTTM'],'current_ratio_v21':['currentRatioQuarterly','currentRatioAnnual'],'interest_coverage_v21':['interestCoverageTTM','interestCoverageAnnual'],'dividend_yield_v21_pct':['currentDividendYieldTTM','dividendYieldIndicatedAnnual'],'beta_v21':['beta'],'high_52w':['52WeekHigh'],'low_52w':['52WeekLow'],'fcf_yield_v21':['freeCashFlowYieldTTM','fcfYieldTTM']}
    for i in candidates:
        audit['attempted']+=1;isin=str(df.at[i,'isin'] or '').strip();ticker=str(df.at[i,'yahoo_ticker'] or '').strip();name=str(df.at[i,'name'] or '').strip()
        try:
            symbol,_=_resolved_symbol(session,cache,isin=isin,yahoo_ticker=ticker,name=name,token=token)
            if not symbol:audit['unresolved']+=1;_fill(df,i,'analyst_coverage_status_v21','UNRESOLVED_FINNHUB_IDENTITY');continue
            audit['resolved']+=1;symbol_to_idx[symbol]=i;_fill(df,i,'finnhub_symbol_v21',symbol);_fill(df,i,'finnhub_checked_at_utc',checked_at)
            reco=_get_json(session,'/stock/recommendation',{'symbol':symbol,'token':token},max_retries=2) or []
            if isinstance(reco,list) and reco:
                current=reco[0] or {};s5,n=_score5(current)
                if s5 is not None:
                    audit['recommendation_observed']+=1
                    for field,val in [('consensus_score_100_v21',_score100(s5)),('consensus_label_v21',_label_from_score(s5)),('n_analysts_v21',n)]:audit['filled_cells']+=int(_fill(df,i,field,val))
                    _fill(df,i,'consensus_source_v21','FINNHUB');_fill(df,i,'analyst_coverage_status_v21','OBSERVED');_fill(df,i,'analyst_coverage_source_v21','FINNHUB')
                    if len(reco)>1:
                        prev5,prevn=_score5(reco[1] or {})
                        if prev5 is not None and prevn>0:audit['filled_cells']+=int(_fill(df,i,'consensus_delta_4w',round(_score100(s5)-_score100(prev5),4)));_fill(df,i,'consensus_score_100_4w_ago_v21',_score100(prev5))
                else:audit['no_analyst_coverage_confirmed']+=1;_fill(df,i,'analyst_coverage_status_v21','NO_ANALYST_COVERAGE_CONFIRMED_FINNHUB');_fill(df,i,'analyst_coverage_source_v21','FINNHUB')
            else:audit['no_analyst_coverage_confirmed']+=1;_fill(df,i,'analyst_coverage_status_v21','NO_ANALYST_COVERAGE_CONFIRMED_FINNHUB');_fill(df,i,'analyst_coverage_source_v21','FINNHUB')
            time.sleep(DELAY)
            if _missing_value(df.at[i,'target_mean_v21'] if 'target_mean_v21' in df else None):
                try:
                    target=_get_json(session,'/stock/price-target',{'symbol':symbol,'token':token},max_retries=1) or {};tmean=_f(target.get('targetMean'))
                    if tmean is not None:
                        audit['target_observed']+=1
                        for field,val in [('target_mean_v21',tmean),('target_low_v21',_f(target.get('targetLow'))),('target_high_v21',_f(target.get('targetHigh'))),('target_median_v21',_f(target.get('targetMedian'))),('n_analysts_v21',_f(target.get('numberAnalysts')))]:audit['filled_cells']+=int(_fill(df,i,field,val))
                        _fill(df,i,'target_source_v21','FINNHUB')
                except Exception as exc:
                    if len(audit['errors'])<60:audit['errors'].append({'ticker':ticker,'stage':'price_target','error':f'{type(exc).__name__}: {str(exc)[:140]}'})
                time.sleep(DELAY)
            if metric_count<METRIC_MAX:
                try:
                    body=_get_json(session,'/stock/metric',{'symbol':symbol,'metric':'all','token':token},max_retries=1) or {};metrics=body.get('metric',{}) if isinstance(body,dict) else {}
                    if metrics:
                        audit['metric_observed']+=1
                        for dst,names in metric_mapping.items():
                            val=_metric(metrics,names)
                            if val is not None:audit['filled_cells']+=int(_fill(df,i,dst,val))
                except Exception as exc:
                    if len(audit['errors'])<60:audit['errors'].append({'ticker':ticker,'stage':'basic_financials','error':f'{type(exc).__name__}: {str(exc)[:140]}'})
                metric_count+=1;time.sleep(DELAY)
        except Exception as exc:
            if len(audit['errors'])<60:audit['errors'].append({'ticker':ticker,'stage':'resolution_or_recommendation','error':f'{type(exc).__name__}: {str(exc)[:160]}'})
    _save_symbol_cache(CACHE,cache)
    try:
        today=datetime.now(timezone.utc).date();cal=_get_json(session,'/calendar/earnings',{'from':today.isoformat(),'to':(today+timedelta(days=120)).isoformat(),'international':'true','token':token},max_retries=2) or {}
        for event in cal.get('earningsCalendar',[]) if isinstance(cal,dict) else []:
            symbol=str(event.get('symbol') or '').strip();i=symbol_to_idx.get(symbol);date=str(event.get('date') or '').strip()
            if i is None or not date:continue
            if _fill(df,i,'next_earnings_date',date):
                audit['filled_cells']+=1
                try:
                    days=(pd.Timestamp(date).date()-today).days;_fill(df,i,'days_to_earnings',days);_fill(df,i,'earnings_window_7d_flag',bool(0<=days<=7));_fill(df,i,'earnings_window_30d_flag',bool(0<=days<=30))
                except Exception:pass
    except Exception as exc:audit['errors'].append({'stage':'earnings_calendar_bulk','error':f'{type(exc).__name__}: {str(exc)[:180]}'})
    try:
        today=datetime.now(timezone.utc).date();events=_get_json(session,'/stock/upgrade-downgrade',{'from':(today-timedelta(days=30)).isoformat(),'to':today.isoformat(),'token':token},max_retries=1) or [];broker_weights=_broker_weight_map();grouped={}
        for e in events if isinstance(events,list) else []:grouped.setdefault(str(e.get('symbol') or '').strip(),[]).append(e)
        for symbol,evs in grouped.items():
            i=symbol_to_idx.get(symbol)
            if i is None:continue
            ups=sum(1 for e in evs if str(e.get('action') or '').lower()=='up');downs=sum(1 for e in evs if str(e.get('action') or '').lower()=='down')
            if ups+downs:audit['filled_cells']+=int(_fill(df,i,'net_upgrades_30d_v21',ups-downs));_fill(df,i,'upgrades_30d_v21',ups);_fill(df,i,'downgrades_30d_v21',downs)
            weighted=total_w=0.0
            for e in evs:
                oldr,newr=_grade_rank(e.get('fromGrade')),_grade_rank(e.get('toGrade'))
                if oldr is None or newr is None:continue
                w=broker_weights.get(str(e.get('company') or '').strip().casefold(),1.0);weighted+=(newr-oldr)*25.0*w;total_w+=w
            if total_w:audit['filled_cells']+=int(_fill(df,i,'broker_weighted_revision_30d',round(weighted/total_w,4)))
    except Exception as exc:audit['errors'].append({'stage':'upgrade_downgrade_bulk','error':f'{type(exc).__name__}: {str(exc)[:180]}'})
    last=pd.to_numeric(df.get('last_close'),errors='coerce');target=pd.to_numeric(df.get('target_mean_v21'),errors='coerce');df['target_upside_pct_v21']=((target/last)-1.0)*100.0;df.loc[last.le(0)|last.isna()|target.isna(),'target_upside_pct_v21']=np.nan;df['potential_gt_15_flag']=df['target_upside_pct_v21'].ge(15).where(df['target_upside_pct_v21'].notna())
    df.to_csv(TARGET,sep=';',index=False,encoding='utf-8-sig');after={f:round(float(df[f].notna().mean()*100),2) if f in df else 0.0 for f in tracked};audit['coverage_before_pct']=before;audit['coverage_after_pct']=after;audit['coverage_gain_points']={f:round(after[f]-before[f],2) for f in tracked};audit['analyst_process_coverage_pct']=round(float((df['consensus_score_100_v21'].notna()|df['analyst_coverage_status_v21'].astype(str).str.startswith('NO_ANALYST_COVERAGE_CONFIRMED')).mean()*100),2);audit['generated_at_utc']=datetime.now(timezone.utc).isoformat();AUDIT.parent.mkdir(parents=True,exist_ok=True);AUDIT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8');print('V21_ACTIONS_FINNHUB_BACKFILL_1829_OK',json.dumps({'attempted':audit['attempted'],'resolved':audit['resolved'],'consensus':after['consensus_score_100_v21'],'target':after['target_mean_v21'],'analyst_process_coverage':audit['analyst_process_coverage_pct']}))

if __name__=='__main__':main()
