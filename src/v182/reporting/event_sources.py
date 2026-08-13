from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from v182.io.frames import is_missing
from v182.sources.amf_short_positions import AMF_SHORT_STABLE_URL, fetch_current_public_shorts
from v182.sources.finnhub_earnings import fetch_earnings_calendar, fetch_eps_estimates, update_eps_history

FINNHUB_EARNINGS_DOC = "https://finnhub.io/docs/api/earnings-calendars"
FINNHUB_ESTIMATES_DOC = "https://finnhub.io/docs/api/analyst-estimates"


def _obs(isin: str, field: str, value, source: str, evidence: str, source_url: str, *, as_of: str | None = None) -> dict:
    now=datetime.now(timezone.utc).isoformat()
    return {
        "universe":"ACTION","isin":isin,"field":field,"value":value,"source":source,
        "source_url":source_url,"collected_at":now,"as_of":as_of or now[:10],
        "evidence_level":evidence,"validation_status":"AUTO_MATCH",
    }


def _clean_symbol(value) -> str:
    return "" if is_missing(value) else str(value).strip().upper()


def _ticker_map(actions_df: pd.DataFrame) -> dict[str,str]:
    candidates=("finnhub_symbol","yahoo_ticker","ticker","euronext_symbol","symbol")
    pairs=[]
    for _,row in actions_df.iterrows():
        isin=str(row.get("isin") or "").strip().upper()
        if not isin:
            continue
        for field in candidates:
            if field not in actions_df.columns:
                continue
            symbol=_clean_symbol(row.get(field))
            if symbol:
                pairs.append((symbol,isin))
    by_symbol={}
    ambiguous=set()
    for symbol,isin in pairs:
        if symbol in by_symbol and by_symbol[symbol]!=isin:
            ambiguous.add(symbol)
        else:
            by_symbol[symbol]=isin
    for symbol in ambiguous:
        by_symbol.pop(symbol,None)

    # Finnhub calendar symbols can omit a Yahoo exchange suffix. Base-symbol
    # fallback is allowed only when the base is unique across the full universe.
    base_to_isins={}
    for symbol,isin in pairs:
        base=symbol.split(".",1)[0]
        base_to_isins.setdefault(base,set()).add(isin)
    for base,isins in base_to_isins.items():
        if len(isins)==1 and base not in by_symbol:
            by_symbol[base]=next(iter(isins))
    return by_symbol


def _primary_finnhub_tickers(actions_df: pd.DataFrame) -> tuple[list[str],dict[str,str]]:
    mapping={}
    for _,row in actions_df.iterrows():
        isin=str(row.get("isin") or "").strip().upper()
        if not isin:
            continue
        symbol=""
        for field in ("finnhub_symbol","yahoo_ticker","ticker","euronext_symbol"):
            if field in actions_df.columns:
                symbol=_clean_symbol(row.get(field))
                if symbol:
                    break
        if symbol and symbol not in mapping:
            mapping[symbol]=isin
    return sorted(mapping),mapping


def collect_finnhub_earnings(
    actions_df: pd.DataFrame,
    api_key: str,
    state_path: str | Path,
    *,
    as_of: date | None = None,
    calendar_days: int = 35,
) -> tuple[list[dict],list[dict],dict]:
    as_of=as_of or date.today(); observations=[]; failures=[]
    events,calendar_failures=fetch_earnings_calendar(
        api_key,from_date=as_of,to_date=as_of+timedelta(days=calendar_days),international=True,
    )
    failures.extend(calendar_failures)
    symbol_to_isin=_ticker_map(actions_df)
    matched_events=0
    best_event_by_isin={}
    for event in events:
        symbol=_clean_symbol(event.get("symbol")); isin=symbol_to_isin.get(symbol)
        if isin is None:
            isin=symbol_to_isin.get(symbol.split(".",1)[0])
        if not isin:
            continue
        try:
            event_date=date.fromisoformat(str(event.get("date"))[:10])
        except ValueError:
            continue
        if event_date<as_of:
            continue
        current=best_event_by_isin.get(isin)
        if current is None or event_date<current[0]:
            best_event_by_isin[isin]=(event_date,event)
    for isin,(event_date,event) in best_event_by_isin.items():
        matched_events+=1; days=(event_date-as_of).days
        fields={
            "earnings_date_finnhub":event_date.isoformat(),
            "days_to_earnings":days,
            "earnings_hour_finnhub":event.get("hour"),
            "earnings_eps_estimate_finnhub":event.get("eps_estimate"),
            "earnings_revenue_estimate_finnhub":event.get("revenue_estimate"),
            "earnings_quarter_finnhub":event.get("quarter"),
            "earnings_year_finnhub":event.get("year"),
            "earnings_within_7d_flag":days<=7,
            "earnings_within_30d_flag":days<=30,
        }
        for field,value in fields.items():
            if value is not None:
                observations.append(_obs(isin,field,value,"Finnhub Earnings Calendar","B",FINNHUB_EARNINGS_DOC,as_of=as_of.isoformat()))

    tickers,ticker_to_isin=_primary_finnhub_tickers(actions_df)
    eps_rows,eps_failures,eps_status=fetch_eps_estimates(tickers,api_key,as_of=as_of)
    failures.extend(eps_failures)
    derived,history_status=update_eps_history(eps_rows,ticker_to_isin,state_path,as_of=as_of)
    for item in derived:
        isin=item["isin"]
        for field,value in item["fields"].items():
            if value is not None:
                observations.append(_obs(isin,field,value,"Finnhub EPS Estimates","B",FINNHUB_ESTIMATES_DOC,as_of=as_of.isoformat()))
    stats={
        "status":"SUCCESS" if not calendar_failures else "PARTIAL_SUCCESS",
        "calendar_events_received":len(events),"calendar_isins_matched":matched_events,
        "calendar_window_days":calendar_days,"eps_estimates":eps_status,"eps_history":history_status,
        "observations":len(observations),"failures":len(failures),"no_revision_imputation":True,
    }
    return observations,failures,stats


def collect_amf_short_positions(actions_df: pd.DataFrame, *, as_of: date | None = None) -> tuple[list[dict],list[dict],dict]:
    as_of=as_of or date.today(); rows,failures,stats=fetch_current_public_shorts(as_of=as_of)
    allowed={str(x).strip().upper() for x in actions_df.get("isin",pd.Series(dtype=str)).dropna()}
    observations=[]; matched=0
    for row in rows:
        isin=str(row.get("isin") or "").strip().upper()
        if isin not in allowed:
            continue
        matched+=1
        for field,value in row.items():
            if field in {"isin","issuer"} or value is None:
                continue
            observations.append(_obs(isin,field,value,"AMF Open Data - Positions courtes nettes","A",AMF_SHORT_STABLE_URL,as_of=as_of.isoformat()))
    stats={**stats,"canonical_action_isins_matched":matched,"observations":len(observations),"evidence_level":"A","absence_means_zero":False}
    return observations,failures,stats
