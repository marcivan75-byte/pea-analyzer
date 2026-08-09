from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
IN=ROOT/'outputs/V20.7_ETF102_COMMITTEE.csv'
MASTER=ROOT/'outputs/V20.7_ETF102_REFERENCE_MASTER.csv'
COMPLETE=ROOT/'outputs/V20.7_ETF102_REFERENCE_COMPLETE.csv'
COVERAGE=ROOT/'outputs/V20.7_ETF102_REFERENCE_COVERAGE.csv'
GAPS=ROOT/'outputs/V20.7_ETF102_REFERENCE_GAPS.csv'
AUDIT=ROOT/'outputs/audit/V20.7_ETF102_REFERENCE_AUDIT.json'
VERSION='V20.7_ETF102_REFERENCE_MASTER'
MISSING_POLICY='VALUE_OR_EXPLICIT_NA_NO_NEUTRAL_50'

MANDATORY=[
 'isin','name','asset_class','pea_type','pea_confidence','provider','category','geo_exposure',
 'ticker_primary','primary_exchange','primary_mic','trading_currency','ticker_identity_status','ticker_confidence_pct',
 'euronext_symbol','yahoo_ticker','referential_status','funnel_global_macro_score',
 'funnel_global_news_score','funnel_country_news_score','funnel_market_sentiment_score','funnel_context_score',
 'funnel_context_coverage','funnel_macro_multiplier','funnel_risk_gate','v207_topdown_score','v207_topdown_coverage',
 'v207_topdown_multiplier','v207_topdown_gate','ifs_effective','smart_money_confidence','smart_money_data_status',
 'score_ct','score_mt','score_lt','score_short','rank_ct','rank_mt','rank_lt','rank_short',
 'decision_ct','decision_mt','decision_lt','decision_short','v207_version','execution','legacy_266_used'
]
HIGH_VALUE=[
 'dividend_yield_pct','distribution_policy','morningstar_rating','ter_pct','fund_total_assets_eur_m','holdings',
 'volatility_1y_pct','max_drawdown_1y','direct_beta3y','risk_indicator','spread_pct','diversification_direct_score',
 'tracking_error_1y_pct','tracking_error_3y_pct','tracking_error_5y_pct','perf_1y_pct','perf_3y_pct','perf_5y_pct',
 'funnel_sector_news_score','funnel_instrument_news_score'
]

def _nonempty(s: pd.Series) -> pd.Series:
 x=s.astype('string')
 return x.notna() & x.str.strip().fillna('').ne('') & ~x.str.lower().isin(['nan','null','<na>'])

def _group(f: str) -> str:
 if f.startswith('v207_topdown_') or f.startswith('funnel_'): return 'TOP_DOWN'
 if f.startswith('ifs_') or f.startswith('smart_money') or f.startswith('flow_') or f=='institutional_flow_label': return 'SMART_MONEY'
 if f.startswith('score_') or f.startswith('rank_') or f.startswith('decision_') or f.startswith('selection_') or f.startswith('effective_weight_') or f.startswith('contrib_') or f.startswith('v207_gate_penalty_'): return 'SCORING_DECISION'
 if f in {'isin','name','asset_class','pea_type','pea_confidence','provider','category','geo_exposure','ticker_primary','primary_exchange','primary_mic','trading_currency','ticker_identity_status','ticker_confidence_pct','euronext_symbol','yahoo_ticker','referential_status'}: return 'IDENTITY'
 if f in {'ter_pct','fund_total_assets_eur_m','dividend_yield_pct','distribution_policy','morningstar_rating','risk_indicator','spread_pct','diversification_direct_score','direct_beta3y','holdings'} or f.startswith('tracking_error_') or f.startswith('direct_'): return 'ETF_STRUCTURE'
 if f.startswith('perf_') or f.startswith('volatility_') or f in {'max_drawdown_1y','relative_strength','macd_hist','rsi14','rvol20','volume','positive_reversal_flag'}: return 'MARKET_TECHNICAL'
 if f in {'execution','legacy_266_used','v207_version','etf102_bonus_malus'}: return 'GOVERNANCE'
 return 'PROVENANCE_OTHER'

def main():
 d=pd.read_csv(IN,sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
 if len(d)!=102 or d['isin'].astype(str).nunique()!=102: raise RuntimeError('V20.7 reference requires exactly 102 ETF')
 if not d['ticker_identity_status'].astype(str).eq('FINAL_VALIDATED').all(): raise RuntimeError('identity gate failed')
 if not d['asset_class'].astype(str).str.upper().eq('ETF').all(): raise RuntimeError('ETF-only gate failed')
 if not d['legacy_266_used'].astype(str).str.lower().eq('false').all(): raise RuntimeError('legacy 266 contamination')
 if not d['execution'].astype(str).eq('RESEARCH_ONLY').all(): raise RuntimeError('execution mode gate failed')
 missing_mand=[f for f in MANDATORY if f not in d.columns or int(_nonempty(d[f]).sum())!=102]
 if missing_mand: raise RuntimeError(f'mandatory V20.7 fields incomplete: {missing_mand}')
 original=list(d.columns); cov=[]
 for f in original:
  populated=int(_nonempty(d[f]).sum()); pct=round(populated/len(d)*100,2)
  cov.append({'field':f,'group':_group(f),'populated':populated,'missing':len(d)-populated,'coverage_pct':pct,'coverage_status':'FULL' if pct==100 else ('HIGH' if pct>=80 else ('PARTIAL' if pct>=50 else ('LOW' if pct>0 else 'UNAVAILABLE'))),'mandatory':f in MANDATORY,'high_value_optional':f in HIGH_VALUE})
 coverage=pd.DataFrame(cov)
 generated=datetime.now(timezone.utc).isoformat(); out=d.copy()
 matrix=pd.DataFrame({c:_nonempty(out[c]) for c in original}); counts=matrix.sum(axis=1)
 out['reference_version']=VERSION; out['reference_generated_at_utc']=generated; out['reference_missing_data_policy']=MISSING_POLICY
 out['reference_populated_fields']=counts.astype(int); out['reference_total_fields']=len(original); out['reference_completeness_pct']=(counts/len(original)*100).round(2)
 miss=[]; status=[]
 for i in out.index:
  m=[f for f in HIGH_VALUE if f in out.columns and not bool(_nonempty(out.loc[[i],f]).iloc[0])]
  miss.append('|'.join(m) if m else 'NONE'); c=float(out.loc[i,'reference_completeness_pct']); status.append('COMPLETE_HIGH' if c>=90 else ('COMPLETE_PARTIAL' if c>=75 else 'DATA_GAPS_REMAIN'))
 out['reference_high_value_missing_fields']=miss; out['reference_status']=status; out['reference_update_note']='ETF_PEA_ONLY; V20.7 criterion-specific CT/MT/LT/Short + V20.7 Top Down + Smart Money negative gate; no legacy 266'
 gaps=[]
 for _,r in coverage[coverage['missing']>0].sort_values(['mandatory','high_value_optional','coverage_pct'],ascending=[False,False,True]).iterrows():
  f=str(r['field']); missing_isins=out.loc[~_nonempty(out[f]),'isin'].astype(str).tolist()
  gaps.append({'field':f,'group':r['group'],'coverage_pct':r['coverage_pct'],'missing_count':r['missing'],'missing_isins':'|'.join(missing_isins),'status':r['coverage_status'],'action':'SOURCE_REQUIRED' if float(r['coverage_pct'])==0 else 'BACKFILL_WHEN_SOURCE_AVAILABLE'})
 gaps_df=pd.DataFrame(gaps)
 audit={'passed':True,'version':VERSION,'generated_at_utc':generated,'rows':len(out),'unique_isin':int(out['isin'].astype(str).nunique()),'legacy_266_used':False,'original_committee_columns':len(original),'reference_columns':len(out.columns),'mandatory_fields':len(MANDATORY),'mandatory_fields_full':len(MANDATORY),'mean_field_coverage_pct':round(float(coverage['coverage_pct'].mean()),2),'full_coverage_fields':int((coverage['coverage_status']=='FULL').sum()),'unavailable_fields':coverage.loc[coverage['coverage_status']=='UNAVAILABLE','field'].astype(str).tolist(),'row_completeness_pct':{'min':round(float(out['reference_completeness_pct'].min()),2),'mean':round(float(out['reference_completeness_pct'].mean()),2),'max':round(float(out['reference_completeness_pct'].max()),2)},'missing_data_policy':MISSING_POLICY,'execution':'RESEARCH_ONLY'}
 MASTER.parent.mkdir(parents=True,exist_ok=True); AUDIT.parent.mkdir(parents=True,exist_ok=True)
 out.to_csv(MASTER,sep=';',index=False,encoding='utf-8-sig')
 complete=out.copy()
 for c in complete.columns:
  mask=_nonempty(complete[c]); complete[c]=complete[c].astype(object).where(mask,'N/A')
 complete.to_csv(COMPLETE,sep=';',index=False,encoding='utf-8-sig')
 coverage.to_csv(COVERAGE,sep=';',index=False,encoding='utf-8-sig'); gaps_df.to_csv(GAPS,sep=';',index=False,encoding='utf-8-sig')
 AUDIT.write_text(json.dumps(audit,indent=2,ensure_ascii=False),encoding='utf-8')
 print('V20_7_ETF102_REFERENCE_OK',json.dumps(audit,ensure_ascii=False))

if __name__=='__main__': main()
