from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd
import numpy as np

from v182.sources.yfinance_bulk import download_history, DownloadResult
from v182.sources.yfinance_info import collect_info, collect_info_cached
from v182.features.ohlcv_features import calculate as calculate_features
from v182.io.frames import is_missing
from v182.mapping.action_yahoo_ticker import qualify_action_yahoo_tickers

NOW = lambda: datetime.now(timezone.utc).isoformat()


def _obs(universe: str, isin: str, field: str, value, source: str, evidence: str) -> dict:
    return {"universe":universe,"isin":isin,"field":field,"value":value,"source":source,"collected_at":NOW(),"as_of":NOW()[:10],"evidence_level":evidence,"validation_status":"AUTO_MATCH"}


def _select_actions_scope(actions_df: pd.DataFrame, cfg: dict, scope_key: str, top_n: int) -> pd.DataFrame:
    scope = str(cfg.get("committee_full_coverage", {}).get(scope_key, "PRIORITY")).upper()
    if scope == "ALL": return actions_df.copy()
    priority_col = "comite_status"
    priority_df = actions_df[actions_df[priority_col].isin(["COMMITTEE", "WATCH"])].copy() if priority_col in actions_df.columns else pd.DataFrame(columns=actions_df.columns)
    if priority_df.empty and "score_brut" in actions_df.columns:
        scored=actions_df.copy(); scored["_score"]=pd.to_numeric(scored["score_brut"],errors="coerce"); priority_df=scored.sort_values("_score",ascending=False).head(top_n)
    return priority_df


def _action_refresh_tiers(actions_df: pd.DataFrame, warm_n: int = 500) -> dict[str, str]:
    """Assign source-refresh priority without changing any trading score."""
    tiers: dict[str,str]={}
    if "yahoo_ticker" not in actions_df.columns:
        return tiers
    for ticker in actions_df["yahoo_ticker"]:
        if not is_missing(ticker): tiers[str(ticker)]="COLD"
    if "score_brut" in actions_df.columns:
        scored=actions_df[["yahoo_ticker","score_brut"]].copy()
        scored["_score"]=pd.to_numeric(scored["score_brut"],errors="coerce")
        scored=scored.dropna(subset=["_score"]).sort_values("_score",ascending=False).head(max(0,int(warm_n)))
        for ticker in scored["yahoo_ticker"]:
            if not is_missing(ticker): tiers[str(ticker)]="WARM"
    if "comite_status" in actions_df.columns:
        hot=actions_df[actions_df["comite_status"].astype(str).str.upper().isin(["COMMITTEE","WATCH"])]
        for ticker in hot["yahoo_ticker"]:
            if not is_missing(ticker): tiers[str(ticker)]="HOT"
    if "earnings_within_30d_flag" in actions_df.columns:
        flags=pd.to_numeric(actions_df["earnings_within_30d_flag"],errors="coerce").fillna(0)
        warm=actions_df[flags.gt(0)]
        for ticker in warm["yahoo_ticker"]:
            if not is_missing(ticker) and tiers.get(str(ticker))!="HOT": tiers[str(ticker)]="WARM"
    if "earnings_within_7d_flag" in actions_df.columns:
        flags=pd.to_numeric(actions_df["earnings_within_7d_flag"],errors="coerce").fillna(0)
        hot=actions_df[flags.gt(0)]
        for ticker in hot["yahoo_ticker"]:
            if not is_missing(ticker): tiers[str(ticker)]="HOT"
    return tiers


def wave_history(df: pd.DataFrame, universe: str, cache_dir: str, cfg: dict) -> DownloadResult:
    if universe == "ACTION": qualify_action_yahoo_tickers(df)
    valid=df[df["yahoo_ticker"].apply(lambda v:not is_missing(v))]; tickers=valid["yahoo_ticker"].tolist(); batch_key="actions_batch_size" if universe=="ACTION" else "etf_batch_size"
    yf_cfg=cfg["yfinance"]
    return download_history(tickers=tickers,cache_dir=cache_dir,period=yf_cfg["history_period"],interval=yf_cfg["interval"],batch_size=yf_cfg[batch_key],auto_adjust=yf_cfg["auto_adjust"],start=yf_cfg.get("history_start","2023-01-01"),rolling_months=int(yf_cfg.get("history_rolling_months",60)))


def resolve_etf_tickers(etf_df: pd.DataFrame, mapping_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    mapping_file=Path(mapping_path)
    if mapping_file.exists():
        mapping=pd.read_csv(mapping_file,sep=";",encoding="utf-8-sig",dtype=str); mapping=mapping[[c for c in ["isin","yahoo_ticker"] if c in mapping.columns]].drop_duplicates("isin")
    else: mapping=pd.DataFrame(columns=["isin","yahoo_ticker"])
    merged=etf_df.copy(); native=merged["yahoo_ticker"].copy() if "yahoo_ticker" in merged.columns else pd.Series(pd.NA,index=merged.index)
    merged=merged.drop(columns=["yahoo_ticker"],errors="ignore").merge(mapping,on="isin",how="left"); merged["yahoo_ticker"]=merged["yahoo_ticker"].where(~merged["yahoo_ticker"].apply(is_missing),native.values)
    gaps=merged[merged["yahoo_ticker"].apply(is_missing)][["isin","name"]].copy(); gaps["status"]="INPUT_REQUIRED"; gaps["reason"]="NO_TICKER_MAPPING"; return merged,gaps


def _history_frames(cache_dir: str) -> list[pd.DataFrame]:
    frames=[]
    for parquet_file in sorted(Path(cache_dir).glob("history_*.parquet")):
        try: frame=pd.read_parquet(parquet_file)
        except Exception: continue
        if not frame.empty: frames.append(frame)
    return frames


def wave3_derived_features(cache_dir: str, ticker_isin_map: dict[str, str], universe: str) -> list[dict]:
    observations=[]; per_ticker_perf_1y={}; per_ticker_perf_10d={}; per_ticker_indicators={}
    for frame in _history_frames(cache_dir):
        if not hasattr(frame.columns,"levels"): continue
        for ticker in frame.columns.get_level_values(0).unique():
            isin=ticker_isin_map.get(ticker)
            if isin is None: continue
            indicators=calculate_features(frame[ticker])
            if not indicators: continue
            per_ticker_indicators[isin]=indicators
            if indicators.get("perf_1y_pct") is not None: per_ticker_perf_1y[isin]=indicators["perf_1y_pct"]
            if indicators.get("perf_10d_pct") is not None: per_ticker_perf_10d[isin]=indicators["perf_10d_pct"]
    median_1y=pd.Series(per_ticker_perf_1y).median() if per_ticker_perf_1y else 0.0; median_10d=pd.Series(per_ticker_perf_10d).median() if per_ticker_perf_10d else 0.0
    for isin,indicators in per_ticker_indicators.items():
        for field,value in indicators.items():
            if value is not None: observations.append(_obs(universe,isin,field,value,"INTERNAL_FROM_OHLCV","C"))
        if indicators.get("perf_1y_pct") is not None: observations.append(_obs(universe,isin,"relative_strength",round(indicators["perf_1y_pct"]-median_1y,4),"INTERNAL_FROM_OHLCV","C"))
        if indicators.get("perf_10d_pct") is not None: observations.append(_obs(universe,isin,"relative_strength_10d",round(indicators["perf_10d_pct"]-median_10d,4),"INTERNAL_FROM_OHLCV","C"))
    return observations


def wave3_etf_beta3y(cache_dir: str, ticker_isin_map: dict[str, str], min_sessions: int = 252) -> list[dict]:
    close_by_ticker={}
    for frame in _history_frames(cache_dir):
        if not hasattr(frame.columns,"levels"): continue
        for ticker in frame.columns.get_level_values(0).unique():
            if ticker not in ticker_isin_map: continue
            sub=frame[ticker]
            if "Close" in sub.columns:
                s=pd.to_numeric(sub["Close"],errors="coerce").dropna()
                if len(s)>=min_sessions: close_by_ticker[ticker]=s
    if len(close_by_ticker)<5: return []
    returns=pd.concat({t:s.pct_change() for t,s in close_by_ticker.items()},axis=1).tail(756); market=returns.mean(axis=1,skipna=True); market_var=float(market.var()) if market.notna().sum()>=min_sessions else np.nan
    if not np.isfinite(market_var) or market_var<=0: return []
    out=[]
    for ticker in returns.columns:
        pair=pd.concat([returns[ticker],market],axis=1).dropna()
        if len(pair)<min_sessions: continue
        beta=float(pair.iloc[:,0].cov(pair.iloc[:,1])/pair.iloc[:,1].var())
        if np.isfinite(beta): out.append(_obs("ETF",ticker_isin_map[ticker],"direct_beta3y",round(beta,6),"INTERNAL_PEA_ETF_EQUAL_WEIGHT_PROXY","C"))
    return out


def _positive_ratio(numerator, denominator) -> float | None:
    try: num=float(numerator); den=float(denominator)
    except (TypeError,ValueError): return None
    if not np.isfinite(num) or not np.isfinite(den) or num<=0 or den<=0: return None
    return round(num/den,6)


def wave4_info_actions(actions_df: pd.DataFrame, cfg: dict, top_n: int = 300) -> tuple[list[dict], list[dict]]:
    selected=_select_actions_scope(actions_df,cfg,"actions_fundamentals_scope",top_n)
    ticker_to_isin={str(t):i for t,i in zip(selected["yahoo_ticker"],selected["isin"]) if not is_missing(t)}
    root=Path(__file__).resolve().parents[3]
    opt=cfg.get("runtime_optimization",{}).get("yfinance_fundamentals",{})
    tiers=_action_refresh_tiers(selected,warm_n=int(opt.get("warm_top_n",500)))
    cache_path=root/str(opt.get("cache_path","state/provenance/source_cache/YFINANCE_INFO_V1.json"))
    observations,failures,metrics=collect_info_cached(list(ticker_to_isin),cache_path,priority_tiers=tiers,ttl_days=opt.get("ttl_days",{"HOT":3,"WARM":7,"COLD":21}),refresh_budget=int(opt.get("refresh_budget",450)),hard_max_age_days=float(opt.get("hard_max_age_days",35)),negative_cache_days=float(opt.get("negative_cache_days",7)),delay_seconds=float(cfg["yfinance"].get("info_delay_seconds",0.4)),max_workers=int(opt.get("max_workers",4)))
    audit_path=root/"outputs"/"audit"/"YFINANCE_INFO_CACHE_V1.json"; audit_path.parent.mkdir(parents=True,exist_ok=True); audit_path.write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding="utf-8")
    result=[]; fields_by_ticker: dict[str,dict[str,object]]={}
    for row in observations:
        ticker=str(row.get("ticker") or ""); isin=ticker_to_isin.get(ticker)
        if isin is None: continue
        field=str(row.get("field") or ""); fields_by_ticker.setdefault(ticker,{})[field]=row.get("value")
        fetched_at=str(row.get("fetched_at_utc") or NOW()); source="yfinance" if row.get("cache_state")=="LIVE_REFRESH" else "yfinance_CACHE"
        result.append({"universe":"ACTION","isin":isin,"field":field,"value":row["value"],"source":source,"collected_at":fetched_at,"as_of":fetched_at[:10],"evidence_level":"C","validation_status":"AUTO_MATCH"})
    last_close_by_ticker={str(t):c for t,c in zip(selected["yahoo_ticker"],selected.get("last_close",pd.Series(index=selected.index,dtype=float))) if not is_missing(t)}
    for ticker,fields in fields_by_ticker.items():
        isin=ticker_to_isin.get(ticker); close=last_close_by_ticker.get(ticker)
        if isin is None: continue
        local={"per_ttm_yf":_positive_ratio(close,fields.get("trailing_eps_yf")),"per_forward_yf":_positive_ratio(close,fields.get("forward_eps_yf")),"pb":_positive_ratio(close,fields.get("book_value_per_share_yf"))}
        for field,value in local.items():
            if value is not None: result.append(_obs("ACTION",isin,field,value,"INTERNAL_OHLCV_X_YF_FUNDAMENTALS","C"))
    return result,failures


def wave5_consensus_finnhub(actions_df: pd.DataFrame, api_key: str, top_n: int = 300, cfg: dict | None = None) -> tuple[list[dict], list[dict]]:
    from v182.sources.finnhub_consensus import fetch_consensus_cached
    selected=actions_df.copy(); ticker_to_isin={str(t):i for t,i in zip(selected["yahoo_ticker"],selected["isin"]) if not is_missing(t)}
    root=Path(__file__).resolve().parents[3]
    if cfg is None:
        try: cfg=json.loads((root/"config"/"V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
        except Exception: cfg={}
    opt=(cfg or {}).get("runtime_optimization",{}).get("finnhub_consensus",{})
    cache_path=root/str(opt.get("cache_path","state/provenance/source_cache/FINNHUB_CONSENSUS_V1.json"))
    tiers=_action_refresh_tiers(selected,warm_n=int(opt.get("warm_top_n",500)))
    tier_policy=opt.get("tiers",{
        "HOT":{"refresh_budget":120,"recommendation_ttl_days":3,"target_refresh_budget":60,"target_ttl_days":7,"max_cache_age_days":14},
        "WARM":{"refresh_budget":80,"recommendation_ttl_days":7,"target_refresh_budget":40,"target_ttl_days":14,"max_cache_age_days":28},
        "COLD":{"refresh_budget":40,"recommendation_ttl_days":14,"target_refresh_budget":20,"target_ttl_days":28,"max_cache_age_days":42},
    })
    obs_raw=[]; failures=[]; tier_metrics={}
    for tier in ("HOT","WARM","COLD"):
        tickers=[ticker for ticker in ticker_to_isin if tiers.get(ticker,"COLD")==tier]
        if not tickers:
            tier_metrics[tier]={"requested":0,"live_refresh_requested":0,"target_live_refresh_requested":0}; continue
        policy=tier_policy.get(tier,{})
        tier_obs,tier_fail,tier_metric=fetch_consensus_cached(tickers,api_key,cache_path,refresh_budget=int(policy.get("refresh_budget",0)),max_cache_age_days=float(policy.get("max_cache_age_days",42)),negative_cache_days=float(opt.get("negative_cache_days",7)),recommendation_ttl_days=float(policy.get("recommendation_ttl_days",14)),target_ttl_days=float(policy.get("target_ttl_days",28)),target_refresh_budget=int(policy.get("target_refresh_budget",max(0,int(policy.get("refresh_budget",0))//2))),delay_seconds=float(opt.get("delay_seconds",1.1)),max_workers=int(opt.get("max_workers",8)))
        obs_raw.extend(tier_obs); failures.extend(tier_fail); tier_metrics[tier]=tier_metric
    metrics={"policy":"HOT_WARM_COLD_INDEPENDENT_RECOMMENDATION_TARGET_TTLS","tier_counts":{tier:sum(1 for value in tiers.values() if value==tier) for tier in ("HOT","WARM","COLD")},"tiers":tier_metrics,"requested":len(ticker_to_isin),"live_refresh_requested":sum(int(m.get("live_refresh_requested",0)) for m in tier_metrics.values()),"target_live_refresh_requested":sum(int(m.get("target_live_refresh_requested",0)) for m in tier_metrics.values()),"target_calls_avoided":sum(int(m.get("target_calls_avoided",0)) for m in tier_metrics.values()),"cache_hit_tickers":sum(int(m.get("cache_hit_tickers",0)) for m in tier_metrics.values()),"full_universe_preserved":True}
    audit_path=root/"outputs"/"audit"/"FINNHUB_CONSENSUS_CACHE_V1.json"; audit_path.parent.mkdir(parents=True,exist_ok=True); audit_path.write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding="utf-8")
    result=[]
    for row in obs_raw:
        isin=ticker_to_isin.get(str(row.get("ticker") or ""))
        if isin is None: continue
        fetched_at=str(row.get("fetched_at_utc") or NOW()); source="Finnhub" if row.get("cache_state")=="LIVE_REFRESH" else "Finnhub_CACHE"
        result.append({"universe":"ACTION","isin":isin,"field":row["field"],"value":row["value"],"source":source,"collected_at":fetched_at,"as_of":fetched_at[:10],"evidence_level":"B","validation_status":"AUTO_MATCH"})
    return result,failures


def _finite_float(value) -> float | None:
    try: number=float(value)
    except (TypeError,ValueError): return None
    return number if np.isfinite(number) else None


def _yahoo_expense_ratio_pct(value) -> float | None:
    number=_finite_float(value)
    if number is None or number<0 or number>1: return None
    return round(number*100.0,6)


def _yahoo_total_assets_eur_m(value, asset_currency) -> float | None:
    number=_finite_float(value)
    if number is None or number<0 or str(asset_currency or "").strip().upper()!="EUR": return None
    return round(number/1_000_000.0,6)


def wave6_etf_info(etf_with_tickers: pd.DataFrame, cfg: dict) -> tuple[list[dict], list[dict]]:
    valid=etf_with_tickers[etf_with_tickers["yahoo_ticker"].apply(lambda v:not is_missing(v))]
    ticker_to_isin=dict(zip(valid["yahoo_ticker"],valid["isin"]))
    obs_raw,failures=collect_info(list(ticker_to_isin),delay_seconds=cfg["yfinance"].get("info_delay_seconds",0.4))
    result=[]
    allowed={"dividend_yield_pct","sector_yf","industry_yf","country_yf","exchange_yf","full_exchange_name_yf","currency_yf","long_name_yf","quote_type_yf","annual_report_expense_ratio_yf","total_assets_yf","fund_family_yf","category_yf","legal_type_yf","beta3y_yf","yield_yf"}
    by_ticker: dict[str,dict[str,object]]={}
    for row in obs_raw:
        ticker=str(row.get("ticker") or ""); field=str(row.get("field") or "")
        if ticker: by_ticker.setdefault(ticker,{})[field]=row.get("value")
        isin=ticker_to_isin.get(ticker)
        if isin is None or field not in allowed: continue
        result.append(_obs("ETF",isin,field,row["value"],"yfinance","C"))
        if field=="dividend_yield_pct": result.append(_obs("ETF",isin,"dividend_data_status","OK","yfinance","C"))
    for ticker,fields in by_ticker.items():
        isin=ticker_to_isin.get(ticker)
        if isin is None: continue
        ter=_yahoo_expense_ratio_pct(fields.get("annual_report_expense_ratio_yf"))
        if ter is not None: result.append(_obs("ETF",isin,"ter_pct",ter,"yfinance:annualReportExpenseRatio","C"))
        assets=_yahoo_total_assets_eur_m(fields.get("total_assets_yf"),fields.get("total_assets_currency_yf"))
        if assets is not None:
            result.append(_obs("ETF",isin,"fund_total_assets_eur_m",assets,"yfinance:totalAssets+explicitAssetCurrency=EUR","C")); result.append(_obs("ETF",isin,"aum_m",assets,"yfinance:totalAssets+explicitAssetCurrency=EUR","C"))
    return result,failures


def wave9_topdown(actions_df: pd.DataFrame, etf_df: pd.DataFrame, cfg: dict, fred_api_key: str | None) -> tuple[list[dict], list[dict], dict]:
    from v182.features.topdown_features import build_topdown
    spec=cfg.get("topdown",{}); result=build_topdown(actions_df,etf_df,fred_api_key=fred_api_key,instrument_news_top_n=int(spec.get("instrument_news_top_n",80))); obs_actions=[]; obs_etf=[]
    for isin,fields in result.action_scores.items():
        for field,value in fields.items():
            source=result.provenance.get(field,"TOPDOWN_INTERNAL"); evidence="B" if source.startswith("FRED") or source.startswith("GDELT") else "C"; obs_actions.append(_obs("ACTION",isin,field,value,source,evidence))
        if "funnel_market_sentiment_score" in fields: obs_actions.append(_obs("ACTION",isin,"sentiment_regime_score",fields["funnel_market_sentiment_score"],"INTERNAL_PIT_BREADTH_MOMENTUM","C"))
    for isin,fields in result.etf_scores.items():
        for field,value in fields.items():
            source=result.provenance.get(field,"TOPDOWN_INTERNAL"); evidence="B" if source.startswith("FRED") or source.startswith("GDELT") else "C"; obs_etf.append(_obs("ETF",isin,field,value,source,evidence))
    return obs_actions,obs_etf,{"global_scores":result.global_scores,"provenance":result.provenance,"details":result.diagnostics}


def wave_public_table(rows: pd.DataFrame, universe: str, field_map: dict[str, str], url_template: str, selectors: dict[str, str], source_name: str, evidence: str, delay_seconds: float = 0.6) -> tuple[list[dict], list[dict]]:
    import time
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError: return [],[{"reason":"MISSING_DEPENDENCY","source":source_name}]
    observations=[]; failures=[]; headers={"User-Agent":"V18.2-Completeness/1.0"}
    for _,row in rows.iterrows():
        symbol=row.get("euronext_symbol") or row.get("yahoo_ticker"); isin=row.get("isin")
        if is_missing(symbol) or is_missing(isin): failures.append({"isin":isin,"reason":"NO_SYMBOL","source":source_name}); continue
        url=url_template.format(symbol=symbol,isin=isin)
        try:
            resp=requests.get(url,headers=headers,timeout=15); resp.raise_for_status(); soup=BeautifulSoup(resp.text,"lxml")
            for field,css_selector in selectors.items():
                node=soup.select_one(css_selector)
                if node is not None: observations.append(_obs(universe,isin,field_map.get(field,field),node.get_text(strip=True),source_name,evidence))
        except Exception as exc: failures.append({"isin":isin,"reason":type(exc).__name__,"detail":str(exc)[:160],"source":source_name})
        time.sleep(delay_seconds)
    return observations,failures


def wave7_official_validation(quarantine: list[dict], overrides_path: str | Path) -> list[dict]:
    overrides_file=Path(overrides_path)
    if not overrides_file.exists(): return []
    overrides=pd.read_csv(overrides_file,sep=";",encoding="utf-8-sig",dtype=str); resolved=[]
    for _,override in overrides.iterrows():
        match=[q for q in quarantine if q["isin"]==override["isin"] and q["field"]==override["field"]]
        if match: resolved.append(_obs(match[0]["universe"],override["isin"],override["field"],override["value"],"Issuer/AMF/Euronext","A"))
    return resolved


def wave8_scenarios(actions_df: pd.DataFrame, shortlist_isins: set[str]) -> list[dict]:
    observations=[]; subset=actions_df[actions_df["isin"].isin(shortlist_isins)]
    for _,row in subset.iterrows():
        isin=row["isin"]
        try: last_close=float(row.get("last_close")); atr14=float(row.get("atr14"))
        except (TypeError,ValueError): continue
        if last_close<=0: continue
        amplitude=atr14/last_close; bull=round(3*amplitude*100,2); bear=round(-3*amplitude*100,2)
        try: base=round(float(row.get("perf_3m_pct"))/2,2)
        except (TypeError,ValueError): base=0.0
        for field,value in {"scenario_bull_pct":bull,"scenario_bear_pct":bear,"scenario_base_pct":base,"asymmetry":round(bull+bear,2),"invalidation_level":round(last_close-2*atr14,4)}.items(): observations.append(_obs("ACTION",isin,field,value,"INTERNAL_SHORTLIST_ENGINE","C"))
    return observations