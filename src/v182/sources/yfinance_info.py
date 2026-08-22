from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import json
import math

from v182.sources.rate_limit import StartRateLimiter

# Raw yfinance fields are kept under stable V18.2 names. The Committee Master
# resolves them to canonical V21 criteria through explicit semantic aliases.
# ETF-specific values are kept raw here and converted only in the ETF wave when
# their units/currency are explicit enough to do so without guessing.
FIELDS = {
    "marketCap":"market_cap",
    "trailingPE":"per_ttm_yf",
    "forwardPE":"per_forward_yf",
    "priceToBook":"pb",
    "trailingEps":"trailing_eps_yf",
    "forwardEps":"forward_eps_yf",
    "bookValue":"book_value_per_share_yf",
    "returnOnEquity":"roe_api",
    "returnOnAssets":"roa",
    "debtToEquity":"debt_to_equity",
    "totalDebt":"total_debt_yf",
    "ebitda":"ebitda_yf",
    "freeCashflow":"free_cash_flow",
    "operatingMargins":"marge_ebit",
    "profitMargins":"marge_nette",
    "revenueGrowth":"revenue_growth_yf",
    "earningsGrowth":"earnings_growth_yf",
    "targetMeanPrice":"target_mean_yf",
    "targetHighPrice":"target_high_yf",
    "targetLowPrice":"target_low_yf",
    "currentPrice":"current_price_yf",
    "numberOfAnalystOpinions":"n_analysts_yf",
    "recommendationMean":"recommendation_mean_yf",
    "recommendationKey":"recommendation_key_yf",
    "beta":"beta",
    "dividendYield":"dividend_yield_pct",
    "dividendRate":"dividend_rate_yf",
    "payoutRatio":"payout_ratio",
    "sector":"sector_yf",
    "industry":"industry_yf",
    "country":"country_yf",
    "exchange":"exchange_yf",
    "fullExchangeName":"full_exchange_name_yf",
    "currency":"currency_yf",
    "longName":"long_name_yf",
    "quoteType":"quote_type_yf",
    "annualReportExpenseRatio":"annual_report_expense_ratio_yf",
    "totalAssets":"total_assets_yf",
    "fundFamily":"fund_family_yf",
    "category":"category_yf",
    "legalType":"legal_type_yf",
    "beta3Year":"beta3y_yf",
    "yield":"yield_yf",
    "earningsTimestamp":"earnings_timestamp_yf",
    "earningsTimestampStart":"earnings_timestamp_start_yf",
    "earningsTimestampEnd":"earnings_timestamp_end_yf",
}

CACHE_VERSION = "YFINANCE_INFO_CACHE_V1"
DEFAULT_TTL_DAYS = {"HOT": 3.0, "WARM": 7.0, "COLD": 21.0}
_TIER_RANK = {"HOT": 0, "WARM": 1, "COLD": 2}


def _future_earnings_fields(info: dict, now_ts: float | None = None) -> dict:
    """Expose objective earnings-calendar fields without inventing catalyst score."""
    now=now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    candidates=[]
    for key in ("earningsTimestampStart","earningsTimestamp","earningsTimestampEnd"):
        value=info.get(key)
        try:
            ts=float(value)
        except (TypeError,ValueError):
            continue
        if ts >= now - 86400:  # tolerate same-day/time-zone publication windows
            candidates.append(ts)
    if not candidates:
        return {}
    next_ts=min(candidates)
    days=(next_ts-now)/86400.0
    return {
        "days_to_earnings":round(days,3),
        "earnings_within_7d_flag":1.0 if 0 <= days <= 7 else 0.0,
        "earnings_within_30d_flag":1.0 if 0 <= days <= 30 else 0.0,
        "next_earnings_timestamp_yf":int(next_ts),
    }


def _collect_one(ticker: str, yf, limiter: StartRateLimiter) -> tuple[list[dict], dict | None]:
    limiter.wait()
    try:
        info = yf.Ticker(ticker).get_info()
        observations=[]
        for source_field, target_field in FIELDS.items():
            value = info.get(source_field)
            if value is not None:
                observations.append({"ticker":ticker,"field":target_field,"value":value,"source":"yfinance"})
        for field,value in _future_earnings_fields(info).items():
            observations.append({"ticker":ticker,"field":field,"value":value,"source":"yfinance"})
        return observations, None
    except Exception as exc:
        return [], {"ticker": ticker, "error": type(exc).__name__, "detail": str(exc)[:160]}


def collect_info(
    tickers: list[str], delay_seconds: float = 0.4, max_workers: int = 4,
) -> tuple[list[dict], list[dict]]:
    """Collect yfinance metadata with bounded concurrency and stable rate cadence.

    `delay_seconds` is the minimum interval between request starts globally,
    rather than dead time after every completed request. Network waits may overlap
    across a small worker pool, but the source request-start cadence is not raised.
    """
    import yfinance as yf

    unique=sorted({x for x in tickers if x})
    if not unique:
        return [], []
    limiter=StartRateLimiter(delay_seconds)
    observations: list[dict]=[]
    failures: list[dict]=[]
    workers=max(1,min(int(max_workers),len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures=[executor.submit(_collect_one,ticker,yf,limiter) for ticker in unique]
        for future in as_completed(futures):
            obs,failure=future.result()
            observations.extend(obs)
            if failure is not None:
                failures.append(failure)
    observations.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("field",""))))
    failures.sort(key=lambda row:str(row.get("ticker","")))
    return observations, failures


def _parse_utc(value: object) -> datetime | None:
    if value is None:
        return None
    text=str(value).strip()
    if not text:
        return None
    try:
        parsed=datetime.fromisoformat(text.replace("Z","+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(entry: dict | None, now: datetime) -> float:
    if not entry:
        return math.inf
    fetched=_parse_utc(entry.get("fetched_at_utc"))
    if fetched is None:
        return math.inf
    return max(0.0,(now-fetched).total_seconds()/86400.0)


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {"version":CACHE_VERSION,"entries":{}}
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version":CACHE_VERSION,"entries":{}}
    if payload.get("version") != CACHE_VERSION or not isinstance(payload.get("entries"),dict):
        return {"version":CACHE_VERSION,"entries":{}}
    return payload


def _save_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    temp.replace(path)


def _entry_rows(entry: dict, ticker: str, cache_state: str, tier: str) -> list[dict]:
    rows=entry.get("observations") if isinstance(entry.get("observations"),list) else []
    fetched_at=str(entry.get("fetched_at_utc") or "")
    return [
        {
            "ticker":ticker,
            "field":row.get("field"),
            "value":row.get("value"),
            "source":"yfinance",
            "fetched_at_utc":fetched_at,
            "cache_state":cache_state,
            "refresh_tier":tier,
        }
        for row in rows
        if isinstance(row,dict) and row.get("field") is not None and row.get("value") is not None
    ]


def collect_info_cached(
    tickers: list[str],
    cache_path: str | Path,
    *,
    priority_tiers: dict[str,str] | None = None,
    ttl_days: dict[str,float] | None = None,
    refresh_budget: int = 450,
    hard_max_age_days: float = 35.0,
    negative_cache_days: float = 7.0,
    delay_seconds: float = 0.4,
    max_workers: int = 4,
    refresh_due: bool = True,
    now: datetime | None = None,
) -> tuple[list[dict],list[dict],dict]:
    """Return full-universe Yahoo info from a persistent HOT/WARM/COLD cache.

    Missing instruments are bootstrapped exhaustively once. Afterwards only due
    entries are refreshed, with HOT before WARM before COLD and a bounded normal
    refresh budget. ``refresh_due=False`` keeps mandatory bootstrap/hard-stale
    recovery but defers ordinary TTL refreshes to a later full run. Cached source
    timestamps are preserved. A transient source failure may reuse a still-valid
    cache entry but never fabricates a new value.
    """
    unique=sorted({str(t).strip() for t in tickers if str(t).strip()})
    current=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    path=Path(cache_path)
    payload=_load_cache(path)
    entries: dict[str,dict]=payload["entries"]
    tiers={ticker:str((priority_tiers or {}).get(ticker,"COLD")).upper() for ticker in unique}
    ttl={**DEFAULT_TTL_DAYS,**{str(k).upper():float(v) for k,v in (ttl_days or {}).items()}}
    for ticker,tier in list(tiers.items()):
        if tier not in _TIER_RANK:
            tiers[ticker]="COLD"

    mandatory=[]
    due=[]
    negative_fresh=set()
    for ticker in unique:
        entry=entries.get(ticker)
        age=_age_days(entry,current)
        status=str((entry or {}).get("status") or "")
        if entry is None or age > float(hard_max_age_days):
            mandatory.append(ticker)
            continue
        if status == "NO_DATA" and age < float(negative_cache_days):
            negative_fresh.add(ticker)
            continue
        tier=tiers[ticker]
        if age >= float(ttl[tier]):
            due.append((int(_TIER_RANK[tier]),-age,ticker))

    due.sort()
    budget=max(0,int(refresh_budget))
    capacity=max(0,budget-len(mandatory))
    due_selected=[ticker for _,_,ticker in due[:capacity]] if refresh_due else []
    selected=list(dict.fromkeys(mandatory+due_selected))

    live_obs,live_failures=([],[])
    if selected:
        live_obs,live_failures=collect_info(selected,delay_seconds=delay_seconds,max_workers=max_workers)
    by_ticker: dict[str,list[dict]]={}
    for row in live_obs:
        ticker=str(row.get("ticker") or "")
        if ticker:
            by_ticker.setdefault(ticker,[]).append(row)
    failures_by_ticker={str(row.get("ticker") or ""):row for row in live_failures if row.get("ticker")}

    refreshed_at=current.isoformat()
    live_success=0
    live_no_data=0
    transient_fallbacks=0
    expired_after_failure=0
    for ticker in selected:
        rows=by_ticker.get(ticker,[])
        if rows:
            entries[ticker]={
                "status":"OK",
                "fetched_at_utc":refreshed_at,
                "observations":[{"field":row.get("field"),"value":row.get("value")} for row in rows if row.get("field") is not None and row.get("value") is not None],
            }
            live_success+=1
            continue
        old=entries.get(ticker)
        if ticker not in failures_by_ticker:
            entries[ticker]={"status":"NO_DATA","fetched_at_utc":refreshed_at,"observations":[]}
            live_no_data+=1
            continue
        if old and _age_days(old,current) <= float(hard_max_age_days):
            transient_fallbacks+=1
            live_failures.append({
                "ticker":ticker,
                "error":"LIVE_REFRESH_FAILED_CACHE_FALLBACK_USED",
                "detail":f"cached_age_days={_age_days(old,current):.2f}",
            })
        else:
            entries.pop(ticker,None)
            expired_after_failure+=1

    payload["updated_at_utc"]=refreshed_at
    payload["policy"]={
        "ttl_days":ttl,
        "refresh_budget":budget,
        "hard_max_age_days":float(hard_max_age_days),
        "negative_cache_days":float(negative_cache_days),
        "bootstrap_uncached_all":True,
        "refresh_due_enabled":bool(refresh_due),
        "priority_order":["HOT","WARM","COLD"],
    }
    _save_cache(path,payload)

    observations=[]
    cache_hits=0
    tier_counts={"HOT":0,"WARM":0,"COLD":0}
    selected_set=set(selected)
    unusable=0
    for ticker in unique:
        entry=entries.get(ticker)
        tier=tiers[ticker]
        tier_counts[tier]+=1
        if not entry or _age_days(entry,current)>float(hard_max_age_days):
            unusable+=1
            continue
        if str(entry.get("status") or "") != "OK":
            continue
        state="LIVE_REFRESH" if ticker in selected_set and ticker in by_ticker else "CACHE_HIT"
        if state=="CACHE_HIT":
            cache_hits+=1
        observations.extend(_entry_rows(entry,ticker,state,tier))

    ages=sorted(_age_days(entries.get(ticker),current) for ticker in unique if entries.get(ticker) and math.isfinite(_age_days(entries.get(ticker),current)))
    p95=None
    if ages:
        idx=min(len(ages)-1,max(0,math.ceil(len(ages)*0.95)-1))
        p95=round(float(ages[idx]),3)
    metrics={
        "cache_version":CACHE_VERSION,
        "requested":len(unique),
        "cache_entries":len(entries),
        "tier_counts":tier_counts,
        "mandatory_refresh_count":len(mandatory),
        "due_refresh_count":len(due),
        "due_refresh_suppressed":max(0,min(len(due),capacity)-len(due_selected)),
        "refresh_due_enabled":bool(refresh_due),
        "live_refresh_requested":len(selected),
        "live_refresh_success":live_success,
        "live_no_data":live_no_data,
        "cache_hit_tickers":cache_hits,
        "negative_cache_hits":len(negative_fresh),
        "transient_cache_fallbacks":transient_fallbacks,
        "expired_after_refresh_failure":expired_after_failure,
        "unusable_tickers":unusable,
        "cache_age_p95_days":p95,
        "refresh_budget":budget,
        "hard_max_age_days":float(hard_max_age_days),
        "full_universe_preserved":True,
        "cached_timestamp_preserved":True,
    }
    observations.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("field",""))))
    live_failures.sort(key=lambda row:str(row.get("ticker","")))
    return observations,live_failures,metrics
