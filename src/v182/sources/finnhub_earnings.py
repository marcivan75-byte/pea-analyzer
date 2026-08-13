from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from v182.sources.rate_limit import StartRateLimiter

FINNHUB_BASE = "https://finnhub.io/api/v1"
EARNINGS_CALENDAR_URL = f"{FINNHUB_BASE}/calendar/earnings"
EPS_ESTIMATE_URL = f"{FINNHUB_BASE}/stock/eps-estimate"


def _iso(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _json_or_error(response) -> tuple[dict | list | None, str | None]:
    try:
        payload = response.json()
    except Exception as exc:
        return None, f"INVALID_JSON:{type(exc).__name__}"
    if isinstance(payload, dict) and payload.get("error"):
        return payload, str(payload.get("error"))
    return payload, None


def fetch_earnings_calendar(
    api_key: str,
    *,
    from_date: date | str | None = None,
    to_date: date | str | None = None,
    international: bool = True,
    requests_module=None,
) -> tuple[list[dict], list[dict]]:
    """Fetch upcoming/historical earnings events in one Finnhub calendar call.

    The free endpoint supports recent history and new updates. Absence of an
    event in the requested window is never converted to a synthetic "no
    earnings" observation.
    """
    if requests_module is None:
        import requests as requests_module

    start = date.today() if from_date is None else date.fromisoformat(_iso(from_date))
    end = start + timedelta(days=35) if to_date is None else date.fromisoformat(_iso(to_date))
    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "international": "true" if international else "false",
        "token": api_key,
    }
    try:
        response = requests_module.get(EARNINGS_CALENDAR_URL, params=params, timeout=20)
        response.raise_for_status()
        payload, api_error = _json_or_error(response)
        if api_error:
            return [], [{"source":"Finnhub Earnings Calendar","reason":"API_ERROR","detail":api_error[:180]}]
        rows = payload.get("earningsCalendar", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return [], [{"source":"Finnhub Earnings Calendar","reason":"INVALID_PAYLOAD"}]
        normalized=[]
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol=str(row.get("symbol") or "").strip()
            event_date=str(row.get("date") or "")[:10]
            if not symbol or not event_date:
                continue
            normalized.append({
                "symbol":symbol,
                "date":event_date,
                "hour":row.get("hour"),
                "eps_actual":row.get("epsActual"),
                "eps_estimate":row.get("epsEstimate"),
                "revenue_actual":row.get("revenueActual"),
                "revenue_estimate":row.get("revenueEstimate"),
                "quarter":row.get("quarter"),
                "year":row.get("year"),
            })
        normalized.sort(key=lambda r:(r["symbol"],r["date"]))
        return normalized, []
    except Exception as exc:
        return [], [{"source":"Finnhub Earnings Calendar","reason":type(exc).__name__,"detail":str(exc)[:180]}]


def _select_eps_row(payload: Any, as_of: date) -> dict | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return None
    candidates=[]
    for row in payload["data"]:
        if not isinstance(row, dict) or row.get("epsAvg") is None:
            continue
        try:
            period=date.fromisoformat(str(row.get("period"))[:10])
        except (TypeError, ValueError):
            continue
        # The nearest fiscal period is the most useful current estimate around
        # reporting season; exact revisions are later computed only against the
        # same period in persisted PIT snapshots.
        candidates.append((abs((period-as_of).days), period, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item:(item[0], item[1]))
    return candidates[0][2]


def _fetch_eps_one(ticker: str, api_key: str, requests_module, limiter: StartRateLimiter, as_of: date) -> tuple[dict | None, dict | None, bool]:
    try:
        limiter.wait()
        response=requests_module.get(
            EPS_ESTIMATE_URL,
            params={"symbol":ticker,"freq":"quarterly","token":api_key},
            timeout=20,
        )
        if response.status_code in {401,402,403}:
            return None,{"ticker":ticker,"source":"Finnhub EPS Estimates","reason":"PREMIUM_ACCESS_REQUIRED_OR_UNAUTHORIZED","http_status":response.status_code},True
        response.raise_for_status()
        payload,api_error=_json_or_error(response)
        if api_error:
            lower=api_error.lower()
            premium=("premium" in lower or "upgrade" in lower or "permission" in lower or "access" in lower)
            reason="PREMIUM_ACCESS_REQUIRED_OR_UNAUTHORIZED" if premium else "API_ERROR"
            return None,{"ticker":ticker,"source":"Finnhub EPS Estimates","reason":reason,"detail":api_error[:180]},premium
        selected=_select_eps_row(payload,as_of)
        if selected is None:
            return None,{"ticker":ticker,"source":"Finnhub EPS Estimates","reason":"NO_EPS_ESTIMATE_DATA"},False
        return {
            "ticker":ticker,
            "period":str(selected.get("period") or "")[:10],
            "eps_avg":selected.get("epsAvg"),
            "eps_high":selected.get("epsHigh"),
            "eps_low":selected.get("epsLow"),
            "number_analysts":selected.get("numberAnalysts"),
            "quarter":selected.get("quarter"),
            "year":selected.get("year"),
        },None,False
    except Exception as exc:
        return None,{"ticker":ticker,"source":"Finnhub EPS Estimates","reason":type(exc).__name__,"detail":str(exc)[:180]},False


def fetch_eps_estimates(
    tickers: list[str],
    api_key: str,
    *,
    as_of: date | None = None,
    delay_seconds: float = 1.1,
    max_workers: int = 8,
    requests_module=None,
) -> tuple[list[dict], list[dict], dict]:
    """Fetch current quarterly EPS estimates with a premium-access circuit breaker.

    Finnhub documents this endpoint as Premium. One initial ticker is used as a
    capability probe. If access is denied, the remaining universe is skipped so
    a free plan does not generate thousands of doomed requests.
    """
    if requests_module is None:
        import requests as requests_module
    unique=sorted({str(t).strip() for t in tickers if str(t).strip()})
    if not unique:
        return [],[],{"status":"NO_TICKERS","requested":0,"observed":0}
    as_of=as_of or date.today()
    limiter=StartRateLimiter(delay_seconds)
    first=unique[0]
    row,failure,hard_denied=_fetch_eps_one(first,api_key,requests_module,limiter,as_of)
    observations=[] if row is None else [row]
    failures=[] if failure is None else [failure]
    if hard_denied:
        return observations,failures,{
            "status":"PREMIUM_ACCESS_REQUIRED_OR_UNAUTHORIZED",
            "requested":len(unique),"attempted":1,"observed":len(observations),"skipped_after_probe":len(unique)-1,
        }

    remaining=unique[1:]
    workers=max(1,min(int(max_workers),len(remaining))) if remaining else 0
    if workers:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures=[executor.submit(_fetch_eps_one,t,api_key,requests_module,limiter,as_of) for t in remaining]
            for future in as_completed(futures):
                item,failed,_denied=future.result()
                if item is not None:
                    observations.append(item)
                if failed is not None:
                    failures.append(failed)
    observations.sort(key=lambda r:(r["ticker"],r["period"]))
    failures.sort(key=lambda r:(str(r.get("ticker","")),str(r.get("reason",""))))
    return observations,failures,{
        "status":"SUCCESS_WITH_POSSIBLE_GAPS",
        "requested":len(unique),"attempted":len(unique),"observed":len(observations),"failed":len(failures),
    }


_HISTORY_COLUMNS=(
    "snapshot_date","isin","ticker","period","eps_avg","eps_high","eps_low","number_analysts","quarter","year",
)


def _revision_pct(current: float, prior: float) -> float | None:
    try:
        current=float(current); prior=float(prior)
    except (TypeError,ValueError):
        return None
    if prior == 0:
        return None
    return (current-prior)/abs(prior)*100.0


def update_eps_history(
    current_rows: list[dict],
    ticker_to_isin: dict[str,str],
    state_path: str | Path,
    *,
    as_of: date | None = None,
) -> tuple[list[dict], dict]:
    """Persist PIT estimate snapshots and derive true same-period revisions.

    A 30d revision requires >=21 calendar days of prior history; a 3m revision
    requires >=75 days. Cross-quarter estimate growth is never mislabeled as an
    analyst revision.
    """
    as_of=as_of or date.today(); path=Path(state_path); path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        try:
            history=pd.read_csv(path,sep=";",encoding="utf-8-sig",dtype=str,low_memory=False)
        except Exception:
            history=pd.DataFrame(columns=_HISTORY_COLUMNS)
    else:
        history=pd.DataFrame(columns=_HISTORY_COLUMNS)
    for col in _HISTORY_COLUMNS:
        if col not in history.columns:
            history[col]=pd.NA

    derived=[]; append_rows=[]
    for row in current_rows:
        ticker=str(row.get("ticker") or "").strip(); isin=str(ticker_to_isin.get(ticker) or "").strip()
        period=str(row.get("period") or "")[:10]
        if not ticker or not isin or not period or row.get("eps_avg") is None:
            continue
        subset=history[(history["isin"].astype(str)==isin)&(history["period"].astype(str)==period)].copy()
        subset["_date"]=pd.to_datetime(subset["snapshot_date"],errors="coerce")
        subset["_eps"]=pd.to_numeric(subset["eps_avg"],errors="coerce")
        current=float(row["eps_avg"])
        fields={
            "eps_estimate_finnhub":current,
            "eps_estimate_high_finnhub":row.get("eps_high"),
            "eps_estimate_low_finnhub":row.get("eps_low"),
            "eps_estimate_analysts_finnhub":row.get("number_analysts"),
            "eps_estimate_period_finnhub":period,
        }
        high=row.get("eps_high"); low=row.get("eps_low")
        try:
            if current != 0 and high is not None and low is not None:
                fields["eps_estimate_dispersion_pct_finnhub"]=(float(high)-float(low))/abs(current)*100.0
        except (TypeError,ValueError):
            pass
        for field,minimum_age in (("eps_revision_30d",21),("eps_revision_3m",75)):
            if subset.empty:
                continue
            cutoff=pd.Timestamp(as_of-timedelta(days=minimum_age))
            eligible=subset[(subset["_date"].notna())&(subset["_date"]<=cutoff)&(subset["_eps"].notna())].sort_values("_date")
            if eligible.empty:
                continue
            revision=_revision_pct(current,float(eligible.iloc[-1]["_eps"]))
            if revision is not None:
                fields[field]=revision
        derived.append({"isin":isin,"ticker":ticker,"fields":fields})
        append_rows.append({
            "snapshot_date":as_of.isoformat(),"isin":isin,"ticker":ticker,"period":period,
            "eps_avg":row.get("eps_avg"),"eps_high":row.get("eps_high"),"eps_low":row.get("eps_low"),
            "number_analysts":row.get("number_analysts"),"quarter":row.get("quarter"),"year":row.get("year"),
        })

    if append_rows:
        combined=pd.concat([history[list(_HISTORY_COLUMNS)],pd.DataFrame(append_rows,columns=_HISTORY_COLUMNS)],ignore_index=True)
        combined=combined.drop_duplicates(["snapshot_date","isin","period"],keep="last")
        combined.to_csv(path,sep=";",index=False,encoding="utf-8-sig")
    return derived,{"state_path":str(path),"snapshots_added":len(append_rows),"derived_rows":len(derived)}
