from __future__ import annotations
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents)>=4 else Path('.')
CONFIG=ROOT/'data/reference/V20.7_ETF102_CONFIG.json'
FUNNEL=ROOT/'data/reference/V20.7_FUNNEL_CONFIG.json'
IN=ROOT/'outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv'
OUT=ROOT/'outputs/audit/V20.7_ETF102_PRECOMMIT_AUDIT.json'

FORBIDDEN_DIRECT={'tracking_error_1y_pct','tracking_error_3y_pct','tracking_error_5y_pct','morningstar_rating','risk_indicator','spread_pct','rank_cat_1y','rank_cat_3y','rank_cat_5y'}
TOPDOWN_FIELDS={'funnel_global_macro_score','funnel_country_macro_score','funnel_global_news_score','funnel_country_news_score','funnel_sector_news_score','funnel_instrument_news_score','funnel_market_sentiment_score'}


def main():
    cfg=json.loads(CONFIG.read_text(encoding='utf-8')); fcfg=json.loads(FUNNEL.read_text(encoding='utf-8'))
    d=pd.read_csv(IN,sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
    findings=[]; passed=True
    def check(ok,code,detail):
        nonlocal passed
        findings.append({'ok':bool(ok),'code':code,'detail':detail})
        if not ok: passed=False
    check(len(d)==102 and d['isin'].astype(str).nunique()==102,'ETF102_UNIVERSE','exactly 102 unique ISIN')
    check(d['ticker_identity_status'].astype(str).eq('FINAL_VALIDATED').all(),'IDENTITY','all FINAL_VALIDATED')
    check(d['asset_class'].astype(str).str.upper().eq('ETF').all(),'ETF_ONLY','all asset_class ETF')
    check(cfg.get('legacy_266_allowed') is False,'NO_LEGACY_266','legacy 266 forbidden')
    check(cfg.get('missing_data_policy')=='RENORMALIZE_OBSERVED_WEIGHTS_NO_NEUTRAL_50','NO_NEUTRAL_50','missing data renormalized')
    for h,w in cfg['direct_weights'].items():
        check(abs(sum(map(float,w.values()))-1.0)<1e-9,f'{h}_SUM','weights sum to 1.0')
        missing=[f for f in w if f not in d.columns and f!='sentiment_regime_score']
        check(not missing,f'{h}_FIELDS',f'missing={missing}')
        forbidden=sorted(set(w)&FORBIDDEN_DIRECT)
        check(not forbidden,f'{h}_NO_SPARSE_DIRECT',f'forbidden_sparse={forbidden}')
        if h!='SHORT':
            overlap=sorted(set(w)&TOPDOWN_FIELDS)
            check(not overlap,f'{h}_NO_TOPDOWN_DOUBLECOUNT',f'overlap={overlap}')
    check(abs(sum(map(float,fcfg['context_weights'].values()))-1.0)<1e-9,'TOPDOWN_SUM','topdown weights sum 1.0')
    check(cfg['smart_money']['positive_score_boost_allowed'] is False,'SMART_MONEY_SHADOW','no positive Smart Money boost')
    mean_cov={}
    for h,w in cfg['direct_weights'].items():
        avail=pd.DataFrame(index=d.index)
        for f,weight in w.items():
            if f=='sentiment_regime_score':
                avail[f]=1.0
            elif f=='distribution_policy':
                x=d[f].astype('string').str.upper(); avail[f]=x.isin(['DIST','ACC_OR_DIST','ACC']).astype(float)
            else:
                avail[f]=pd.to_numeric(d[f],errors='coerce').notna().astype(float) if f!='positive_reversal_flag' else d[f].notna().astype(float)
        cov=sum(avail[f]*float(w[f]) for f in w)
        mean_cov[h]=round(float(cov.mean()),4)
        check(mean_cov[h]>=0.75,f'{h}_MEAN_COVERAGE',f'mean weighted coverage={mean_cov[h]:.4f}')
    result={'passed':passed,'version':cfg['version'],'rows':len(d),'unique_isin':int(d['isin'].astype(str).nunique()),'findings':findings,'mean_weighted_input_coverage':mean_cov,'execution':'RESEARCH_ONLY'}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print('V20_7_PRECOMMIT_AUDIT',json.dumps(result,ensure_ascii=False))
    if not passed: raise SystemExit(1)

if __name__=='__main__': main()
