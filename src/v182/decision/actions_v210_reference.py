from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json,numpy as np,pandas as pd

ROOT=Path(__file__).resolve().parents[3]
CONFIG=ROOT/'data/reference/V21.0_ACTIONS_PEA_CONFIG.json'; FUNNEL=ROOT/'data/reference/V21.0_ACTIONS_FUNNEL_CONFIG.json'
IN=ROOT/'outputs/V21.0_ACTIONS_PEA_1429_COMMITTEE.csv'; MASTER=ROOT/'outputs/V21.0_ACTIONS_PEA_REFERENCE_MASTER.csv'; COMPLETE=ROOT/'outputs/V21.0_ACTIONS_PEA_REFERENCE_COMPLETE.csv'
COVERAGE=ROOT/'outputs/V21.0_ACTIONS_PEA_REFERENCE_COVERAGE.csv'; GAPS=ROOT/'outputs/V21.0_ACTIONS_PEA_REFERENCE_GAPS.csv'; DICTIONARY=ROOT/'outputs/V21.0_ACTIONS_PEA_CRITERIA_DICTIONARY.csv'; WEIGHTS=ROOT/'outputs/V21.0_ACTIONS_PEA_WEIGHTS.csv'; AUDIT=ROOT/'outputs/audit/V21.0_ACTIONS_PEA_REFERENCE_AUDIT.json'
VERSION='V21.0_ACTIONS_PEA_REFERENCE_MASTER'; MISSING='VALUE_OR_EXPLICIT_NA_NO_NEUTRAL_50'
MANDATORY=['isin','name','asset_class','country','pea_type','pea_confidence','yahoo_ticker','v182_ticker_validation_confidence_pct','canonical_universe','canonical_validation','canonical_execution_guard','v210_version','pea_validation_gate','identity_gate','decision_ct','decision_mt','decision_lt','decision_short','selection_ct','selection_mt','selection_lt','selection_short','T0','T1_1_4w','T2_1_3m','T3_3_6m','T4_6_12m','T5_12_24m','execution','backtest_12m_status','backtest_18m_status','backtest_36m_status']
HIGH=['last_close','volume','market_cap','perf_1m_pct','perf_3m_pct','perf_6m_pct','perf_1y_pct','perf_3y_pct','perf_5y_pct','relative_strength','rsi14','macd_hist','rvol20','max_drawdown_1y','volatility_20d','volatility_60d','high_52w','distance_to_52w_high_pct','stoch_k','per_forward_v21','pb_v21','fcf_yield_v21','roe_v21_pct','roa_v21_pct','debt_to_ebitda_v21','operating_margin_v21_pct','revenue_growth_v21_pct','earnings_growth_v21_pct','dividend_yield_v21_pct','payout_ratio_v21_pct','dividend_cagr_3y','distribution_policy','target_upside_pct_v21','consensus_score_100_v21','consensus_delta_4w','net_upgrades_30d_v21','broker_weighted_revision_30d','next_earnings_date','days_to_earnings','news_catalyst_score','action_topdown_score','action_smart_money_score','action_smart_money_confidence']

def nonempty(s):
    x=s.astype('string'); return x.notna()&x.str.strip().fillna('').ne('')&~x.str.lower().isin(['nan','none','null','<na>','n/a'])

def group(f):
    q=f.lower()
    if f in {'isin','name','country','pea_type','pea_confidence','yahoo_ticker','euronext_symbol','euronext_mic','asset_class','region','markets','sector_v21','sector_yf','industry_yf','sector_yahoo','industry_yahoo'} or 'ticker_validation' in q or q.startswith('canonical_'): return 'IDENTITY'
    if q.startswith(('mm','macd','bb_','perf_','stoch_','distance_to_52','high_52','low_52','volume','rvol','volatility','max_drawdown','relative_strength','above_','atr','breakout_','positive_reversal')) or f in {'last_close','beta','beta_v21'}: return 'MARKET_TECHNICAL'
    if q.startswith(('per_','pb_','ev_','fcf_yield','valuation_')) or f=='enterprise_value_v21': return 'VALUATION'
    if any(k in q for k in ['roe','roa','margin','marge','growth','croiss','debt','dette','cash_flow','cashflow','ebitda','current_ratio','interest_coverage']): return 'FUNDAMENTALS'
    if 'dividend' in q or 'payout' in q or f=='distribution_policy': return 'DIVIDEND_INCOME'
    if any(k in q for k in ['consensus','target','analyst','upgrade','downgrade','revision','recommendation','broker_weighted']): return 'ANALYST_CONSENSUS'
    if any(k in q for k in ['earnings','news','guidance','warning','buyback','mna_rumor','corporate_event']): return 'CATALYST_NEWS'
    if q.startswith(('action_topdown','funnel_','fear_greed','aaii_','macro_','sentiment_regime')): return 'TOP_DOWN'
    if any(k in q for k in ['smart_money','insider','whale','short_seller','short_percent','short_ratio','significant_holder','public_short','wis_','cmf20','obv','ad_slope']): return 'SMART_MONEY_INSIDERS'
    if q.startswith(('score_','rank_','decision_','selection_','effective_weight_','contrib_','weight_coverage_','t0','t1_','t2_','t3_','t4_','t5_')): return 'SCORING_DECISION'
    if q.startswith(('reference_','backtest_')) or f in {'execution','v210_version','pea_validation_gate','identity_gate','liquidity_percentile'}: return 'GOVERNANCE'
    return 'PROVENANCE_OTHER'

def calc(f):
    q=f.lower(); return q.startswith(('score_','rank_','decision_','selection_','effective_weight_','contrib_','weight_coverage_','reference_','action_topdown','t0','t1_','t2_','t3_','t4_','t5_')) or f in {'target_upside_pct_v21','potential_gt_15_flag','valuation_discount_score','liquidity_percentile','pea_validation_gate','identity_gate','earnings_catalyst_score','earnings_risk_gate','sentiment_regime_score','distance_to_52w_high_pct','distance_to_52w_low_pct','stoch_k','stoch_d','stoch_bull_cross_flag','stoch_bear_cross_flag','volume_avg_20d','volume_acceleration_20d','breakout_20d_flag','volatility_1y_pct','fcf_yield_v21','debt_to_ebitda_v21'}

def source(g):
    return {'IDENTITY':('Référentiel PEA canonique + Euronext','Yahoo Finance / validation ticker'),'MARKET_TECHNICAL':('Historique marché validé + Yahoo Finance','Euronext'),'VALUATION':('Yahoo Finance + fondamentaux canoniques','Finnhub / MarketBeat / société'),'FUNDAMENTALS':('Yahoo Finance + fondamentaux canoniques','Finnhub / publications société'),'DIVIDEND_INCOME':('Yahoo Finance + données canoniques','Société / Euronext / Boursorama'),'ANALYST_CONSENSUS':('Yahoo Finance + MarketBeat + historique consensus','Finnhub + brokers pondérés'),'CATALYST_NEWS':('GDELT + Google News RSS + calendrier Yahoo','Communiqués société / réglementaires'),'TOP_DOWN':('FRED + ECB + Eurostat + CNN Fear & Greed + AAII','GDELT / Google News'),'SMART_MONEY_INSIDERS':('V18.3 Smart Money shadow + données publiques insiders/short','AMF / volumes / sources institutionnelles'),'SCORING_DECISION':('Moteur V21.0 Actions','Audits GitHub Actions'),'GOVERNANCE':('Moteur V21.0 Actions','Audits GitHub Actions'),'PROVENANCE_OTHER':('Référentiel hérité V18.2/V20.4','Provenance par champ')}[g]

def frequency(g):
    return {'IDENTITY':'Mensuelle + changement référentiel','MARKET_TECHNICAL':'Chaque clôture / run','VALUATION':'Chaque run prioritaire + publication','FUNDAMENTALS':'Trimestrielle + publication','DIVIDEND_INCOME':'Annonce / mensuelle','ANALYST_CONSENSUS':'Chaque run + événement analyste','CATALYST_NEWS':'Chaque run / fenêtre 2 jours','TOP_DOWN':'Chaque run','SMART_MONEY_INSIDERS':'Jour ouvré / run shadow','SCORING_DECISION':'Chaque run','GOVERNANCE':'Chaque run','PROVENANCE_OTHER':'Selon source'}[g]

def formula(f):
    m={'target_upside_pct_v21':'(objectif moyen / dernier cours - 1) × 100','potential_gt_15_flag':'TRUE si potentiel objectif > 15%','fcf_yield_v21':'free cash-flow / capitalisation × 100','debt_to_ebitda_v21':'dette totale / EBITDA','valuation_discount_score':'45% rang inverse PER forward + 20% rang inverse P/B + 35% rang FCF yield, sector-neutral et renormalisé','action_topdown_score':'15% macro global +15% macro pays +10% news monde +10% news pays +15% news secteur +20% news société +15% sentiment, renormalisé sur observations','liquidity_percentile':'rang percentile du volume dans les 1429 actions','distance_to_52w_high_pct':'(cours / plus-haut 52 semaines - 1) × 100','distance_to_52w_low_pct':'(cours / plus-bas 52 semaines - 1) × 100','stoch_k':'(close-low14)/(high14-low14)×100','stoch_d':'MM3 du stochastic K','volatility_1y_pct':'écart-type rendements quotidiens × √252 ×100'}
    if f in m:return m[f]
    if f.startswith('weight_coverage_'): return 'somme des poids des critères réellement observés / 100%'
    if f.startswith('effective_weight_'): return 'poids théorique / somme des poids observés pour la ligne'
    if f.startswith('contrib_'): return 'score percentile/transformé du critère × poids effectif'
    if f.startswith('score_'): return 'agrégation renormalisée des critères observés + pénalités/gates, puis calibration percentile selon V21'
    if f.startswith('rank_'): return 'rang décroissant du score final dans les 1429 actions'
    if f.startswith('decision_'): return 'seuil de score puis gates PEA/identité/couverture/liquidité/Top Down/publications/Smart Money'
    return '—' if not calc(f) else 'Règle calculée par le moteur V21.0 Actions'

def valueadd(g):
    return {'IDENTITY':'Sécurise éligibilité PEA et rattachement ticker; gate indispensable, faible alpha direct.','MARKET_TECHNICAL':'Mesure tendance, retournement, timing, liquidité et risque de marché; très discriminant CT/MT.','VALUATION':'Mesure la décote relative et le prix payé; essentielle pour détecter potentiel non encore reflété.','FUNDAMENTALS':'Mesure qualité, croissance, rentabilité et solidité financière; centrale MT/LT.','DIVIDEND_INCOME':'Mesure revenu distribué et soutenabilité; surtout patrimonial LT.','ANALYST_CONSENSUS':'Capte potentiel et changements d’opinion; priorité aux révisions plutôt qu’au consensus statique.','CATALYST_NEWS':'Capte résultats, guidance, contrats, OPA/rachat et alertes susceptibles de provoquer une revalorisation.','TOP_DOWN':'Filtre le régime macro/pays/secteur/société avant décision bottom-up; évite les BUY contre contexte.','SMART_MONEY_INSIDERS':'Confirmation institutionnelle/insiders; contribution positive verrouillée, garde-fou négatif tant que walk-forward incomplet.','SCORING_DECISION':'Synthèse opérationnelle; ne crée pas une information indépendante et ne doit pas être double-comptée.','GOVERNANCE':'Assure reproductibilité, couverture, N/A explicites et absence d’exécution live.','PROVENANCE_OTHER':'Traçabilité ou contexte complémentaire.'}[g]

def build(root:Path|None=None):
    root=root or ROOT; cfg=json.loads((root/CONFIG.relative_to(ROOT)).read_text()); fun=json.loads((root/FUNNEL.relative_to(ROOT)).read_text())
    df=pd.read_csv(root/IN.relative_to(ROOT),sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
    if len(df)!=1429 or df['isin'].astype(str).nunique()!=1429: raise RuntimeError('V21 reference universe gate')
    generated=datetime.now(timezone.utc).isoformat(); original=list(df.columns)
    missing=[f for f in MANDATORY if f not in df or int(nonempty(df[f]).sum())!=1429]
    if missing: raise RuntimeError('Mandatory V21 fields incomplete: '+str(missing))
    matrix=pd.DataFrame({c:nonempty(df[c]) for c in original}); counts=matrix.sum(axis=1)
    df['reference_version']=VERSION; df['reference_generated_at_utc']=generated; df['reference_missing_data_policy']=MISSING; df['reference_populated_fields']=counts.astype(int); df['reference_total_fields']=len(original); df['reference_completeness_pct']=(counts/len(original)*100).round(2)
    hv=[f for f in HIGH if f in df]; df['reference_high_value_missing_fields']=['|'.join([f for f in hv if not bool(nonempty(df.loc[[i],f]).iloc[0])]) or 'NONE' for i in df.index]
    df['reference_status']=np.where(df['reference_completeness_pct'].ge(85),'COMPLETE_HIGH',np.where(df['reference_completeness_pct'].ge(65),'COMPLETE_PARTIAL','DATA_GAPS_REMAIN')); df['reference_update_note']='PEA_ACTIONS_1429_ONLY; V21 direct criterion weights + Top Down + analyst revisions + Smart Money shadow; no neutral 50; no live execution'
    cov=pd.DataFrame([{'field':f,'group':group(f),'populated':int(nonempty(df[f]).sum()),'missing':int((~nonempty(df[f])).sum()),'coverage_pct':round(float(nonempty(df[f]).mean()*100),2),'mandatory':f in MANDATORY,'high_value_optional':f in HIGH} for f in df.columns])
    tdmap={'funnel_global_macro_score':'global_macro','funnel_country_macro_score':'country_macro','funnel_global_news_score':'global_news','funnel_country_news_score':'country_news','funnel_sector_news_score':'sector_news','funnel_instrument_news_score':'instrument_news','funnel_market_sentiment_score':'market_sentiment'}; drows=[]
    for f in df.columns:
        g=group(f); p,s=source(g); cp=float(cov.loc[cov.field.eq(f),'coverage_pct'].iloc[0]); td=next((float(w) for k,w in fun['context_weights'].items() if tdmap.get(f)==k),0.0)
        drows.append({'Libellé du critère':f,'Nom en clair':f.replace('_',' ').strip().capitalize(),'Groupe':g,'Données acquises':f"Source/calcul — couverture {cp:.2f}%" if calc(f) else f"Oui — couverture {cp:.2f}%",'Source prioritaire':p,'Source secondaire':s,'Périodicité d actualisation':frequency(g),'Donnée calculée':'Oui' if calc(f) else 'Non','Formule':formula(f),'Fiabilité':('Très élevée' if g in {'IDENTITY','GOVERNANCE'} else ('Élevée' if cp>=80 else ('Moyenne' if cp>=50 else 'Limitée / conditionnelle')))+f' — couverture {cp:.2f}%','Commentaire sur la réelle valeur ajoutée du critère':valueadd(g),'Poids CT':float(cfg['horizon_weights']['CT'].get(f,0)),'Poids MT':float(cfg['horizon_weights']['MT'].get(f,0)),'Poids LT':float(cfg['horizon_weights']['LT'].get(f,0)),'Poids Short':float(cfg['short_weights'].get(f,0)),'Poids Top Down':td})
    dictionary=pd.DataFrame(drows); weights=dictionary[['Libellé du critère','Nom en clair','Groupe','Poids CT','Poids MT','Poids LT','Poids Short','Poids Top Down']].copy(); gaps=[]
    for _,r in cov[cov.missing.gt(0)].sort_values(['mandatory','high_value_optional','coverage_pct'],ascending=[False,False,True]).iterrows():
        f=r.field; miss=df.loc[~nonempty(df[f]),'isin'].astype(str).tolist(); gaps.append({'field':f,'group':r.group,'coverage_pct':r.coverage_pct,'missing_count':r.missing,'missing_isins':'|'.join(miss),'status':'UNAVAILABLE' if r.coverage_pct==0 else 'PARTIAL','action':'SOURCE_REQUIRED' if r.coverage_pct==0 else 'BACKFILL_WHEN_SOURCE_AVAILABLE'})
    gaps=pd.DataFrame(gaps); complete=df.astype(object).copy()
    for c in complete.columns: complete.loc[~nonempty(complete[c]),c]='N/A'
    blank=sum(int(complete[c].astype(str).str.strip().eq('').sum()) for c in complete.columns)
    for path,frame in [(MASTER,df),(COMPLETE,complete),(COVERAGE,cov),(GAPS,gaps),(DICTIONARY,dictionary),(WEIGHTS,weights)]:
        p=root/path.relative_to(ROOT); p.parent.mkdir(parents=True,exist_ok=True); frame.to_csv(p,sep=';',index=False,encoding='utf-8-sig')
    audit={'passed':True,'version':VERSION,'rows':len(df),'unique_isin':int(df['isin'].nunique()),'source_columns':len(original),'reference_columns':len(df.columns),'criteria_dictionary_rows':len(dictionary),'mandatory_fields':len(MANDATORY),'mandatory_fields_full':len(MANDATORY)-len(missing),'mean_field_coverage_pct':round(float(cov.coverage_pct.mean()),2),'full_coverage_fields':int(cov.coverage_pct.eq(100).sum()),'high_value_coverage':{f:round(float(nonempty(df[f]).mean()*100),2) for f in HIGH if f in df},'unavailable_fields':cov.loc[cov.coverage_pct.eq(0),'field'].tolist(),'missing_data_policy':MISSING,'complete_view_blank_cells':blank,'execution':'RESEARCH_ONLY','generated_at_utc':generated}
    p=root/AUDIT.relative_to(ROOT); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('V21_ACTIONS_REFERENCE_OK',{'rows':len(df),'cols':len(df.columns),'dictionary':len(dictionary),'coverage':audit['mean_field_coverage_pct']}); return audit

if __name__=='__main__': build()
