from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd

from v182.sources.yfinance_bulk import download_history, DownloadResult
from v182.sources.yfinance_info import collect_info, FIELDS as INFO_FIELDS
from v182.features.ohlcv_features import calculate as calculate_features
from v182.io.frames import is_missing

NOW = lambda: datetime.now(timezone.utc).isoformat()


def _obs(universe: str, isin: str, field: str, value, source: str, evidence: str) -> dict:
    return {
        "universe": universe,
        "isin": isin,
        "field": field,
        "value": value,
        "source": source,
        "collected_at": NOW(),
        "as_of": NOW()[:10],
        "evidence_level": evidence,
        "validation_status": "AUTO_MATCH",
    }


def _select_actions_scope(actions_df: pd.DataFrame, cfg: dict, scope_key: str, top_n: int) -> pd.DataFrame:
    """Select the Actions collection scope without silently truncating Committee runs."""
    scope = str(cfg.get("committee_full_coverage", {}).get(scope_key, "PRIORITY")).upper()
    if scope == "ALL":
        return actions_df.copy()
    priority_col = "comite_status"
    if priority_col in actions_df.columns:
        priority_df = actions_df[actions_df[priority_col].isin(["COMMITTEE", "WATCH"])].copy()
    else:
        priority_df = pd.DataFrame(columns=actions_df.columns)
    if priority_df.empty and "score_brut" in actions_df.columns:
        scored = actions_df.copy()
        scored["_score"] = pd.to_numeric(scored["score_brut"], errors="coerce")
        priority_df = scored.sort_values("_score", ascending=False).head(top_n)
    return priority_df


# ---------------------------------------------------------------- WAVE 01/02
def wave_history(df: pd.DataFrame, universe: str, cache_dir: str, cfg: dict) -> DownloadResult:
    valid = df[df["yahoo_ticker"].apply(lambda v: not is_missing(v))]
    tickers = valid["yahoo_ticker"].tolist()
    batch_key = "actions_batch_size" if universe == "ACTION" else "etf_batch_size"
    return download_history(tickers=tickers, cache_dir=cache_dir, period=cfg["yfinance"]["history_period"], interval=cfg["yfinance"]["interval"], batch_size=cfg["yfinance"][batch_key], auto_adjust=cfg["yfinance"]["auto_adjust"])


def resolve_etf_tickers(etf_df: pd.DataFrame, mapping_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    mapping_file = Path(mapping_path)
    if mapping_file.exists():
        mapping = pd.read_csv(mapping_file, sep=";", encoding="utf-8-sig", dtype=str)
        mapping = mapping[[c for c in ["isin", "yahoo_ticker"] if c in mapping.columns]].drop_duplicates("isin")
    else:
        mapping = pd.DataFrame(columns=["isin", "yahoo_ticker"])
    merged = etf_df.copy()
    native = merged["yahoo_ticker"].copy() if "yahoo_ticker" in merged.columns else pd.Series(pd.NA, index=merged.index)
    merged = merged.drop(columns=["yahoo_ticker"], errors="ignore").merge(mapping, on="isin", how="left")
    merged["yahoo_ticker"] = merged["yahoo_ticker"].where(~merged["yahoo_ticker"].apply(is_missing), native.values)
    gaps = merged[merged["yahoo_ticker"].apply(is_missing)][["isin", "name"]].copy()
    gaps["status"] = "INPUT_REQUIRED"; gaps["reason"] = "NO_TICKER_MAPPING"
    return merged, gaps


# ---------------------------------------------------------------- WAVE 03
def wave3_derived_features(cache_dir: str, ticker_isin_map: dict[str, str], universe: str) -> list[dict]:
    observations: list[dict] = []
    per_ticker_perf_1y: dict[str, float] = {}
    per_ticker_indicators: dict[str, dict] = {}
    cache = Path(cache_dir)
    for parquet_file in sorted(cache.glob("history_*.parquet")):
        frame = pd.read_parquet(parquet_file)
        if not hasattr(frame.columns, "levels"):
            continue
        tickers = frame.columns.get_level_values(0).unique()
        for ticker in tickers:
            isin = ticker_isin_map.get(ticker)
            if isin is None: continue
            indicators = calculate_features(frame[ticker])
            if not indicators: continue
            per_ticker_indicators[isin] = indicators
            if indicators.get("perf_1y_pct") is not None: per_ticker_perf_1y[isin] = indicators["perf_1y_pct"]
    median_perf = pd.Series(per_ticker_perf_1y).median() if per_ticker_perf_1y else 0.0
    for isin, indicators in per_ticker_indicators.items():
        for field, value in indicators.items():
            if value is not None: observations.append(_obs(universe, isin, field, value, "INTERNAL_FROM_OHLCV", "C"))
        if indicators.get("perf_1y_pct") is not None:
            observations.append(_obs(universe, isin, "relative_strength", round(indicators["perf_1y_pct"] - median_perf, 3), "INTERNAL_FROM_OHLCV", "C"))
    return observations


# ---------------------------------------------------------------- WAVE 04
def wave4_info_actions(actions_df: pd.DataFrame, cfg: dict, top_n: int = 300) -> tuple[list[dict], list[dict]]:
    """Collect fundamentals on ALL Actions when Committee full coverage is enabled."""
    selected = _select_actions_scope(actions_df, cfg, "actions_fundamentals_scope", top_n)
    ticker_to_isin = {t: i for t, i in zip(selected["yahoo_ticker"], selected["isin"]) if not is_missing(t)}
    observations, failures = collect_info(list(ticker_to_isin), delay_seconds=cfg["yfinance"].get("info_delay_seconds", 0.4))
    result=[]
    for row in observations:
        isin=ticker_to_isin.get(row["ticker"])
        if isin is not None: result.append(_obs("ACTION", isin, row["field"], row["value"], "yfinance", "C"))
    return result, failures


# ---------------------------------------------------------------- WAVE 05
def wave5_consensus_finnhub(actions_df: pd.DataFrame, api_key: str, top_n: int = 300) -> tuple[list[dict], list[dict]]:
    """Collect Finnhub consensus over the full canonical Action universe.

    The caller is the Committee enrichment pipeline; source-side missing data
    remains missing and later coverage gates decide whether a title is scorable.
    """
    from v182.sources.finnhub_consensus import fetch_consensus
    selected = actions_df.copy()
    ticker_to_isin = {t: i for t, i in zip(selected["yahoo_ticker"], selected["isin"]) if not is_missing(t)}
    obs_raw, failures = fetch_consensus(list(ticker_to_isin), api_key)
    result=[]
    for row in obs_raw:
        isin=ticker_to_isin.get(row["ticker"])
        if isin is not None: result.append(_obs("ACTION", isin, row["field"], row["value"], "Finnhub", "B"))
    return result, failures


# ---------------------------------------------------------------- WAVE 06
def wave6_etf_info(etf_with_tickers: pd.DataFrame, cfg: dict) -> tuple[list[dict], list[dict]]:
    valid = etf_with_tickers[etf_with_tickers["yahoo_ticker"].apply(lambda v: not is_missing(v))]
    ticker_to_isin = dict(zip(valid["yahoo_ticker"], valid["isin"]))
    obs_raw, failures = collect_info(list(ticker_to_isin), delay_seconds=cfg["yfinance"].get("info_delay_seconds", 0.4))
    result=[]
    for row in obs_raw:
        isin=ticker_to_isin.get(row["ticker"])
        if isin is None or row["field"] not in {"dividend_yield_pct"}: continue
        result.append(_obs("ETF", isin, row["field"], row["value"], "yfinance", "C"))
        result.append(_obs("ETF", isin, "dividend_data_status", "OK", "yfinance", "C"))
    return result, failures


# ---------------------------------------------------- WAVE 05/06 fallback
def wave_public_table(rows: pd.DataFrame, universe: str, field_map: dict[str, str], url_template: str, selectors: dict[str, str], source_name: str, evidence: str, delay_seconds: float = 0.6) -> tuple[list[dict], list[dict]]:
    import time
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return [], [{"reason": "MISSING_DEPENDENCY", "source": source_name}]
    observations=[]; failures=[]; headers={"User-Agent":"V18.2-Completeness/1.0"}
    for _, row in rows.iterrows():
        symbol=row.get("euronext_symbol") or row.get("yahoo_ticker"); isin=row.get("isin")
        if is_missing(symbol) or is_missing(isin):
            failures.append({"isin":isin,"reason":"NO_SYMBOL","source":source_name}); continue
        url=url_template.format(symbol=symbol, isin=isin)
        try:
            resp=requests.get(url,headers=headers,timeout=15); resp.raise_for_status(); soup=BeautifulSoup(resp.text,"lxml")
            for field,css_selector in selectors.items():
                node=soup.select_one(css_selector)
                if node is not None: observations.append(_obs(universe,isin,field_map.get(field,field),node.get_text(strip=True),source_name,evidence))
        except Exception as exc:
            failures.append({"isin":isin,"reason":type(exc).__name__,"source":source_name})
        time.sleep(delay_seconds)
    return observations, failures


# ---------------------------------------------------------------- WAVE 07
def wave7_official_validation(quarantine: list[dict], overrides_path: str | Path) -> list[dict]:
    overrides_file=Path(overrides_path)
    if not overrides_file.exists(): return []
    overrides=pd.read_csv(overrides_file,sep=";",encoding="utf-8-sig",dtype=str); resolved=[]
    for _,override in overrides.iterrows():
        match=[q for q in quarantine if q["isin"]==override["isin"] and q["field"]==override["field"]]
        if match: resolved.append(_obs(match[0]["universe"],override["isin"],override["field"],override["value"],"Issuer/AMF/Euronext","A"))
    return resolved


# ---------------------------------------------------------------- WAVE 08
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
        for field,value in {"scenario_bull_pct":bull,"scenario_bear_pct":bear,"scenario_base_pct":base,"asymmetry":round(bull+bear,2),"invalidation_level":round(last_close-2*atr14,4)}.items():
            observations.append(_obs("ACTION",isin,field,value,"INTERNAL_SHORTLIST_ENGINE","C"))
    return observations
