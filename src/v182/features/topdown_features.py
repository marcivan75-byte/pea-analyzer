from __future__ import annotations
from dataclasses import dataclass
import math
import pandas as pd

from v182.sources.fred_macro import global_macro_score
from v182.sources.gdelt_news import score_query, safe_query_text, pause


@dataclass(frozen=True)
class TopDownResult:
    global_scores: dict[str, float]
    action_scores: dict[str, dict[str, float]]
    etf_scores: dict[str, dict[str, float]]
    provenance: dict[str, str]
    diagnostics: list[dict]


def _num(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def _clamp(value: float) -> float:
    return float(max(0.0, min(100.0, value)))


def _market_regime_score(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    p1=_num(frame,"perf_1m_pct")
    p6=_num(frame,"perf_6m_pct")
    values=[]
    if p1.notna().sum() >= 10:
        breadth=float((p1.dropna()>0).mean()*100.0)
        median=float(p1.median())
        values.append(0.55*breadth + 0.45*_clamp(50.0+median*4.0))
    if p6.notna().sum() >= 10:
        breadth=float((p6.dropna()>0).mean()*100.0)
        median=float(p6.median())
        values.append(0.60*breadth + 0.40*_clamp(50.0+median*1.5))
    return round(sum(values)/len(values),4) if values else None


def _country_series(frame: pd.DataFrame) -> pd.Series:
    for field in ("country_yf","country","listing_country","country_domicile","geo_exposure"):
        if field in frame.columns and frame[field].notna().any():
            return frame[field].astype(str).str.strip().replace({"":"N/A","nan":"N/A"})
    return pd.Series("N/A",index=frame.index)


def _sector_series(frame: pd.DataFrame) -> pd.Series:
    for field in ("sector_yf","sector_v21","sector","industry_yf","industry","category","morningstar_category"):
        if field in frame.columns and frame[field].notna().any():
            return frame[field].astype(str).str.strip().replace({"":"N/A","nan":"N/A"})
    return pd.Series("N/A",index=frame.index)


def _group_regime_scores(frame: pd.DataFrame, groups: pd.Series, min_names: int = 3) -> dict[str,float]:
    out={}
    tmp=frame.copy(); tmp["__group"]=groups
    for group,sub in tmp.groupby("__group",dropna=False):
        key=str(group).strip()
        if not key or key.upper() in {"N/A","NA","NONE"} or len(sub)<min_names:
            continue
        score=_market_regime_score(sub)
        if score is not None:
            out[key]=score
    return out


def _query_score(query: str, diagnostics: list[dict], kind: str, key: str) -> float | None:
    score,error=score_query(query,timespan="2d",max_records=50)
    diagnostics.append({"kind":kind,"key":key,"query":query,"articles":score.article_count,"positive_hits":score.positive_hits,"negative_hits":score.negative_hits,"score":score.score,"error":error})
    pause()
    return score.score


def _instrument_candidates(frame: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if frame.empty or top_n<=0:
        return frame.iloc[0:0]
    ranked=frame.copy()
    if "market_cap" in ranked.columns:
        ranked["__priority"]=pd.to_numeric(ranked["market_cap"],errors="coerce")
        return ranked.sort_values("__priority",ascending=False).head(top_n)
    return ranked.head(top_n)


def build_topdown(actions: pd.DataFrame, etfs: pd.DataFrame, *, fred_api_key: str | None, instrument_news_top_n: int = 80) -> TopDownResult:
    """Build the currently supportable Top-Down funnel without neutral imputation.

    - Global macro: FRED when available; otherwise a clearly labelled
      market-implied fallback so the Committee does not confuse it with an
      official macro feed.
    - Country macro: market-implied country regime proxy until ECB/Eurostat
      country feeds are directly connected.
    - News: GDELT lexical scores over a 2-day window; no article/evidence means
      the criterion remains missing.
    - Market sentiment: cross-sectional breadth/momentum from the current PIT
      market data, not a fabricated 50/100.
    """
    diagnostics=[]; provenance={}; global_scores={}; action_scores={}; etf_scores={}
    combined=pd.concat([actions.assign(__asset="ACTION"),etfs.assign(__asset="ETF")],ignore_index=True,sort=False)

    sentiment=_market_regime_score(combined)
    if sentiment is not None:
        global_scores["funnel_market_sentiment_score"]=sentiment
        provenance["funnel_market_sentiment_score"]="INTERNAL_PIT_BREADTH_MOMENTUM"

    macro=global_macro_score(fred_api_key)
    if macro.score is not None and macro.coverage>=0.50:
        global_scores["funnel_global_macro_score"]=macro.score
        provenance["funnel_global_macro_score"]="FRED"
    else:
        fallback=_market_regime_score(combined)
        if fallback is not None:
            global_scores["funnel_global_macro_score"]=fallback
            provenance["funnel_global_macro_score"]="MARKET_IMPLIED_MACRO_FALLBACK_C"
    diagnostics.append({"kind":"global_macro","score":macro.score,"coverage":macro.coverage,"components":macro.components,"errors":macro.errors,"effective_source":provenance.get("funnel_global_macro_score")})

    global_news=_query_score("(markets OR economy OR stocks OR bonds)",diagnostics,"global_news","GLOBAL")
    if global_news is not None:
        global_scores["funnel_global_news_score"]=global_news
        provenance["funnel_global_news_score"]="GDELT_2D_LEXICAL"

    for asset_class,frame,target in (("ACTION",actions,action_scores),("ETF",etfs,etf_scores)):
        if frame.empty or "isin" not in frame.columns:
            continue
        countries=_country_series(frame); sectors=_sector_series(frame)
        country_macro=_group_regime_scores(frame,countries,min_names=3)
        country_news={}; sector_news={}
        for country in sorted(set(countries)-{"N/A"}):
            query=f'"{safe_query_text(country)}" (economy OR markets OR rates OR inflation)'
            score=_query_score(query,diagnostics,f"{asset_class}_country_news",country)
            if score is not None: country_news[country]=score
        for sector in sorted(set(sectors)-{"N/A"}):
            query=f'"{safe_query_text(sector)}" (stocks OR industry OR earnings OR outlook)'
            score=_query_score(query,diagnostics,f"{asset_class}_sector_news",sector)
            if score is not None: sector_news[sector]=score

        instrument_news={}
        if asset_class=="ACTION":
            for _,row in _instrument_candidates(frame,instrument_news_top_n).iterrows():
                isin=str(row.get("isin","") or "")
                name=safe_query_text(row.get("name",""))
                if not isin or len(name)<3: continue
                score=_query_score(f'"{name}"',diagnostics,"ACTION_instrument_news",isin)
                if score is not None: instrument_news[isin]=score

        for idx,row in frame.iterrows():
            isin=str(row.get("isin","") or "")
            if not isin: continue
            item={}
            country=str(countries.loc[idx]) if idx in countries.index else "N/A"
            sector=str(sectors.loc[idx]) if idx in sectors.index else "N/A"
            if country in country_macro:
                item["funnel_country_macro_score"]=country_macro[country]
            if country in country_news:
                item["funnel_country_news_score"]=country_news[country]
            if sector in sector_news:
                item["funnel_sector_news_score"]=sector_news[sector]
            if isin in instrument_news:
                item["funnel_instrument_news_score"]=instrument_news[isin]
                item["news_catalyst_score"]=instrument_news[isin]
            for key,value in global_scores.items():
                item[key]=value
            target[isin]=item

    provenance["funnel_country_macro_score"]="MARKET_IMPLIED_COUNTRY_REGIME_C"
    provenance["funnel_country_news_score"]="GDELT_2D_LEXICAL"
    provenance["funnel_sector_news_score"]="GDELT_2D_LEXICAL"
    provenance["funnel_instrument_news_score"]="GDELT_2D_LEXICAL_TOP_ACTIONS"
    return TopDownResult(global_scores,action_scores,etf_scores,provenance,diagnostics)
