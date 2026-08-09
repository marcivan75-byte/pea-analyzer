from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from v182.sources.funnel_context import _us_macro, _ecb_deposit, _weighted_context
from v182.sources.eurostat_hicp_current import eurostat_hicp
from v182.sources.market_sentiment import collect_market_sentiment
from v182.sources.news_resilient import news_score

ROOT=Path(__file__).resolve().parents[3]
CONFIG=ROOT/'data/reference/V21.0_ACTIONS_FUNNEL_CONFIG.json'
TARGET=ROOT/'outputs/V21.0_ACTIONS_PEA_1429_PREPARED.csv'
CONTEXT=ROOT/'outputs/V21.0_ACTIONS_TOPDOWN_CONTEXT.csv'
AUDIT=ROOT/'outputs/audit/V21.0_ACTIONS_TOPDOWN_AUDIT.json'

EURO_CODES={'FR','DE','IT','ES','NL','BE','AT','FI','PT','IE','GR','LU','CY','MT'}
LABELS={'FR':'France','DE':'Germany','IT':'Italy','ES':'Spain','NL':'Netherlands','BE':'Belgium','AT':'Austria','FI':'Finland','PT':'Portugal','IE':'Ireland','GR':'Greece','LU':'Luxembourg','CY':'Cyprus','MT':'Malta','NO':'Norway','SE':'Sweden','DK':'Denmark','US':'United States','EU':'Europe'}


def _code(value, cfg):
    x=str(value or '').strip().upper()
    return cfg.get('country_aliases',{}).get(x, x[:2] if len(x)==2 else 'GLOBAL')


def _parallel(jobs, cfg, workers=10):
    if not jobs: return {}
    out={}
    with ThreadPoolExecutor(max_workers=min(workers,max(1,len(jobs)))) as pool:
        fmap={pool.submit(news_score,q,cfg):k for k,q in jobs.items()}
        for f in as_completed(fmap):
            k=fmap[f]
            try: out[k]=f.result()
            except Exception as exc: out[k]={'status':'ERROR','score':None,'articles':0,'error':f'{type(exc).__name__}: {str(exc)[:160]}'}
    return out


def _sentiment_value(sent):
    fg=sent['fear_greed']['score']; spread=sent['aaii']['bull_bear_spread']
    pieces=[]
    if fg is not None:
        pieces.append(70.0 if 45<=fg<=65 else (45.0 if fg<25 else (40.0 if fg>75 else 55.0)))
    if spread is not None:
        pieces.append(65.0 if spread < -20 else (40.0 if spread > 30 else 58.0))
    return float(np.mean(pieces)) if pieces else None


def apply(root: Path|None=None) -> dict:
    root=root or ROOT
    cfg=json.loads((root/CONFIG.relative_to(ROOT)).read_text(encoding='utf-8'))
    path=root/TARGET.relative_to(ROOT)
    df=pd.read_csv(path,sep=';',dtype=object,encoding='utf-8-sig',low_memory=False)
    if len(df)!=1429 or df['isin'].astype(str).nunique()!=1429: raise RuntimeError('Actions Top Down requires canonical 1429')

    us=_us_macro(cfg); ecb=_ecb_deposit(cfg)
    codes=sorted({_code(v,cfg) for v in df['country']})
    euro=[c for c in codes if c in EURO_CODES]
    hicp={}
    with ThreadPoolExecutor(max_workers=min(10,max(1,len(euro)))) as pool:
        fm={pool.submit(eurostat_hicp,c,cfg):c for c in euro}
        for f in as_completed(fm):
            try: hicp[fm[f]]=f.result()
            except Exception as exc: hicp[fm[f]]={'status':'ERROR','error':str(exc)[:160]}
    eu_infs=[x.get('inflation_score') for x in hicp.values() if x.get('status')=='OK' and x.get('inflation_score') is not None]
    eu_inf=float(np.mean(eu_infs)) if eu_infs else None
    eu_rate=ecb.get('direction_score') if ecb.get('status')=='OK' else None
    eu_score=float(np.mean([x for x in [eu_inf,eu_rate] if x is not None])) if any(x is not None for x in [eu_inf,eu_rate]) else None
    us_score=us.get('score') if us.get('status')=='OK' else None
    global_macro=float(np.mean([x for x in [us_score,eu_score] if x is not None])) if any(x is not None for x in [us_score,eu_score]) else None

    global_news=news_score('(economy OR inflation OR interest rates OR recession OR growth OR central bank OR geopolitics)',cfg)
    country_jobs={c:f'"{LABELS.get(c,c)}" (economy OR rates OR stocks OR earnings OR market)' for c in codes if c!='GLOBAL'}
    country_news=_parallel(country_jobs,cfg)

    # Sector news is genuinely sector-specific when Yahoo sector/industry metadata exists.
    sector_series=df.get('sector_v21',pd.Series(index=df.index,dtype=object)).fillna('').astype(str).str.strip()
    top_sectors=[x for x in sector_series.value_counts().head(20).index if x and x.lower()!='nan']
    sector_news=_parallel({s:f'"{s}" (stocks OR sector OR earnings OR demand OR outlook)' for s in top_sectors},cfg)

    # Company news is intentionally restricted to the enriched shortlist. Missing stays N/A and is renormalized.
    pr=df.get('v210_enrichment_priority',pd.Series(False,index=df.index)).astype(str).str.lower().isin({'true','1','yes'})
    priority=df.loc[pr].copy().sort_values('v210_enrichment_priority_score',ascending=False).head(int(cfg['news']['top_instruments_for_specific_news']))
    instrument_jobs={str(r['isin']):f'"{str(r.get("name") or "").strip()}" (earnings OR guidance OR contract OR acquisition OR warning OR buyback)' for _,r in priority.iterrows() if str(r.get('name') or '').strip()}
    instrument_news=_parallel(instrument_jobs,cfg,workers=12)

    sentiment=collect_market_sentiment(); sentiment_score=_sentiment_value(sentiment)
    fg=sentiment['fear_greed']; aa=sentiment['aaii']
    for key,val in {
        'fear_greed_index':fg['score'],'fear_greed_rating':fg['rating'],'fear_greed_asof':fg['asof'],'fear_greed_source':fg['source'],
        'aaii_bullish_pct':aa['bullish_pct'],'aaii_neutral_pct':aa['neutral_pct'],'aaii_bearish_pct':aa['bearish_pct'],
        'aaii_bull_bear_spread':aa['bull_bear_spread'],'aaii_asof':aa['asof'],'aaii_source':aa['source'],
        'sentiment_data_status':sentiment['status'],'sentiment_collected_at_utc':sentiment['collected_at_utc']
    }.items(): df[key]=val

    weights=cfg['context_weights']; ctx=[]
    for i,row in df.iterrows():
        code=_code(row.get('country'),cfg)
        if code=='US': country_macro=us_score
        elif code in hicp:
            inf=hicp[code].get('inflation_score') if hicp[code].get('status')=='OK' else None
            country_macro=float(np.mean([x for x in [inf,eu_rate] if x is not None])) if any(x is not None for x in [inf,eu_rate]) else None
        elif code in EURO_CODES or code=='EU': country_macro=eu_score
        else: country_macro=global_macro
        cnews=country_news.get(code,{}).get('score') if code!='GLOBAL' else global_news.get('score')
        sector=str(row.get('sector_v21') or '').strip(); snews=sector_news.get(sector,{}).get('score') if sector else None
        inews=instrument_news.get(str(row.get('isin')),{}).get('score')
        vals={'global_macro':global_macro,'country_macro':country_macro,'global_news':global_news.get('score'),'country_news':cnews,'sector_news':snews,'instrument_news':inews,'market_sentiment':sentiment_score}
        score,cov=_weighted_context(vals,weights)
        if score is None:
            ct_mult=mt_mult=1.0; gate='DATA_REQUIRED'
        else:
            def mult(bounds):
                lo,hi=float(bounds['min']),float(bounds['max']); m=lo+(hi-lo)*score/100.0
                if cov<float(cfg['minimum_context_coverage_for_positive_multiplier']) and m>1.0: m=1.0
                return m
            ct_mult=mult(cfg['context_multiplier_ct']); mt_mult=mult(cfg['context_multiplier_mt'])
            gate='BLOCK_BUY' if score<float(cfg['risk_gates']['block_buy_below']) else ('REVIEW_ONLY' if score<float(cfg['risk_gates']['review_only_below']) else 'PASS')
        ctx.append({'isin':row['isin'],'action_country_code':code,'funnel_global_macro_score':global_macro,'funnel_country_macro_score':country_macro,'funnel_global_news_score':global_news.get('score'),'funnel_country_news_score':cnews,'funnel_sector_news_score':snews,'funnel_instrument_news_score':inews,'funnel_market_sentiment_score':sentiment_score,'action_topdown_score':score,'action_topdown_coverage':round(cov,4),'action_topdown_multiplier_ct':round(ct_mult,4),'action_topdown_multiplier_mt':round(mt_mult,4),'action_topdown_gate':gate,'news_catalyst_score':inews})
    cdf=pd.DataFrame(ctx)
    out=df.drop(columns=[c for c in cdf.columns if c!='isin' and c in df.columns],errors='ignore').merge(cdf,on='isin',how='left')
    # Short regime score: adverse context => high weakness. It is observed only if Top Down score exists.
    td=pd.to_numeric(out['action_topdown_score'],errors='coerce'); out['sentiment_regime_score']=(100.0-td).where(td.notna())
    out.to_csv(path,sep=';',index=False,encoding='utf-8-sig'); cdf.to_csv(root/CONTEXT.relative_to(ROOT),sep=';',index=False,encoding='utf-8-sig')
    audit={'passed':True,'version':cfg['version'],'rows':len(out),'weights':weights,'weight_sum':sum(weights.values()),'global_macro':{'score':global_macro,'us':us,'ecb':ecb,'eurostat_hicp':hicp},'global_news':global_news,'instrument_news_queried':len(instrument_jobs),'sector_news_queried':len(top_sectors),'mean_context_coverage':round(float(pd.to_numeric(cdf['action_topdown_coverage'],errors='coerce').mean()),4),'risk_gates':cdf['action_topdown_gate'].value_counts().to_dict(),'sentiment':sentiment,'source_contracts':{'macro_us':'FRED','inflation_eu':'EUROSTAT','rates_euro_area':'ECB','news':'GDELT_PRIMARY+GOOGLE_NEWS_RSS_FALLBACK','sentiment':'CNN_FEAR_GREED+AAII'},'collected_at_utc':datetime.now(timezone.utc).isoformat()}
    ap=root/AUDIT.relative_to(ROOT); ap.parent.mkdir(parents=True,exist_ok=True); ap.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('V21_ACTIONS_TOPDOWN_OK',{'coverage':audit['mean_context_coverage'],'gates':audit['risk_gates'],'instrument_news':len(instrument_jobs)})
    return audit

if __name__=='__main__': apply()
