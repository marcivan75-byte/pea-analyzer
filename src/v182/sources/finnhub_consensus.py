from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import re

from v182.sources.rate_limit import StartRateLimiter

FINNHUB_BASE = "https://finnhub.io/api/v1"
_SCORE_WEIGHTS = {"strongBuy": 5, "buy": 4, "hold": 3, "sell": 2, "strongSell": 1}
CACHE_VERSION = "FINNHUB_CONSENSUS_CACHE_V2"
LEGACY_CACHE_VERSION = "FINNHUB_CONSENSUS_CACHE_V1"
TARGET_FIELDS = {"target_price", "target_last_updated"}
_NO_DATA_REASONS = {"NO_RECOMMENDATION_DATA", "EMPTY_RECOMMENDATION_COUNTS"}
_AUTH_DENIED_REASON = "FINNHUB_AUTH_OR_ENTITLEMENT_DENIED"
_SOURCE_BLOCKED_REASON = "FINNHUB_SOURCE_DISABLED_AUTH_OR_ENTITLEMENT"
_TARGET_AUTH_DENIED_REASON = "FINNHUB_TARGET_AUTH_OR_ENTITLEMENT_DENIED"
_TARGET_SOURCE_BLOCKED_REASON = "FINNHUB_TARGET_SOURCE_DISABLED_AUTH_OR_ENTITLEMENT"
_SECRET_QUERY_RE = re.compile(r"([?&](?:token|api[_-]?key|apikey|key)=)[^&\s]+", re.IGNORECASE)
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+")


def _sanitize_detail(value: object, limit: int = 160) -> str:
    """Remove credentials from exception/HTTP text before it reaches logs/artifacts."""
    text=str(value or "")
    text=_SECRET_QUERY_RE.sub(r"\1<REDACTED>",text)
    text=_BEARER_RE.sub(r"\1<REDACTED>",text)
    return text[:limit]


def _label_from_score(score: float) -> str:
    if score >= 4.5: return "STRONG_BUY"
    if score >= 3.5: return "BUY"
    if score >= 2.5: return "HOLD"
    if score >= 1.5: return "SELL"
    return "STRONG_SELL"


def _counts(row: dict) -> dict[str,int]:
    return {key:int(row.get(key,0) or 0) for key in _SCORE_WEIGHTS}


def _score_from_counts(counts: dict[str,int]) -> float | None:
    total=sum(counts.values())
    if total <= 0: return None
    return sum(counts[key]*weight for key,weight in _SCORE_WEIGHTS.items())/total


def _period(row: dict) -> date | None:
    raw=row.get("period")
    if not raw: return None
    try: return date.fromisoformat(str(raw)[:10])
    except ValueError: return None


def _previous_monthish(reco: list[dict], latest: dict) -> dict | None:
    latest_date=_period(latest)
    if latest_date is None: return reco[1] if len(reco)>1 else None
    for row in reco[1:]:
        observed=_period(row)
        if observed is not None and (latest_date-observed).days >= 21: return row
    return reco[1] if len(reco)>1 else None


def _fetch_one(
    ticker: str,
    api_key: str,
    requests,
    limiter: StartRateLimiter,
    *,
    fetch_target: bool = True,
) -> tuple[list[dict],list[dict]]:
    failures: list[dict]=[]
    try:
        limiter.wait()
        reco_resp=requests.get(
            f"{FINNHUB_BASE}/stock/recommendation",
            params={"symbol":ticker,"token":api_key},
            timeout=15,
        )
        if reco_resp.status_code in {401,403}:
            return [],[{"ticker":ticker,"reason":_AUTH_DENIED_REASON,"detail":f"http_status={reco_resp.status_code}; endpoint=stock/recommendation"}]
        reco_resp.raise_for_status()
        reco=reco_resp.json()
        if not reco: return [],[{"ticker":ticker,"reason":"NO_RECOMMENDATION_DATA"}]
        latest=reco[0]
        counts=_counts(latest)
        score=_score_from_counts(counts)
        if score is None: return [],[{"ticker":ticker,"reason":"EMPTY_RECOMMENDATION_COUNTS"}]
        score=round(score,4)
        rating=_label_from_score(score)
        total=sum(counts.values())
        fields: dict[str,object]={
            "consensus":rating,
            "consensus_rating":rating,
            "consensus_score":score,
            "consensus_period":latest.get("period"),
            "buy_n":counts["strongBuy"]+counts["buy"],
            "hold_n":counts["hold"],
            "sell_n":counts["strongSell"]+counts["sell"],
            "n_analysts":total,
            "consensus_status":"OK",
            "consensus_source":"Finnhub",
        }
        previous=_previous_monthish(reco,latest)
        if previous is not None:
            previous_counts=_counts(previous)
            previous_score=_score_from_counts(previous_counts)
            if previous_score is not None:
                delta_100=(score-previous_score)*20.0
                current_net=(counts["strongBuy"]+counts["buy"])-(counts["sell"]+counts["strongSell"])
                previous_net=(previous_counts["strongBuy"]+previous_counts["buy"])-(previous_counts["sell"]+previous_counts["strongSell"])
                fields.update({
                    "consensus_previous_period":previous.get("period"),
                    "consensus_delta_4w":round(delta_100,4),
                    "net_upgrades_30d":int(current_net-previous_net),
                    "broker_weighted_revision_30d":round(delta_100,4),
                })

        if fetch_target:
            limiter.wait()
            target_resp=requests.get(
                f"{FINNHUB_BASE}/stock/price-target",
                params={"symbol":ticker,"token":api_key},
                timeout=15,
            )
            if target_resp.status_code in {401,403}:
                failures.append({"ticker":ticker,"reason":_TARGET_AUTH_DENIED_REASON,"detail":f"http_status={target_resp.status_code}; endpoint=stock/price-target"})
            elif target_resp.ok:
                target=target_resp.json() or {}
                if target.get("targetMean") is not None: fields["target_price"]=target["targetMean"]
                if target.get("lastUpdated"): fields["target_last_updated"]=target["lastUpdated"]
            else:
                failures.append({"ticker":ticker,"reason":"FINNHUB_TARGET_HTTP_ERROR","detail":f"http_status={target_resp.status_code}; endpoint=stock/price-target"})

        observations=[{"ticker":ticker,"field":field,"value":value,"source":"Finnhub"} for field,value in fields.items() if value is not None]
        return observations,failures
    except Exception as exc:
        failures.append({"ticker":ticker,"reason":type(exc).__name__,"detail":_sanitize_detail(exc)})
        return [],failures


def fetch_consensus(
    tickers: list[str],
    api_key: str,
    delay_seconds: float = 1.1,
    max_workers: int = 8,
    *,
    target_tickers: set[str] | None = None,
) -> tuple[list[dict],list[dict]]:
    """Collect recommendations and only the price targets explicitly requested.

    ``target_tickers=None`` preserves legacy behavior and requests a target for
    every ticker. A target entitlement failure trips a target-only circuit while
    recommendation collection continues.
    """
    import requests

    unique=sorted({str(t).strip() for t in tickers if str(t).strip()})
    if not unique: return [],[]
    target_set=set(unique) if target_tickers is None else {str(t).strip() for t in target_tickers if str(t).strip() in set(unique)}
    limiter=StartRateLimiter(delay_seconds)
    observations: list[dict]=[]
    failures: list[dict]=[]

    # Prefer a target-due ticker for the preflight so both endpoint entitlements
    # are checked with at most one initial instrument.
    first=sorted(target_set)[0] if target_set else unique[0]
    first_obs,first_failures=_fetch_one(first,api_key,requests,limiter,fetch_target=first in target_set)
    observations.extend(first_obs); failures.extend(first_failures)
    if any(str(row.get("reason") or "")==_AUTH_DENIED_REASON for row in first_failures):
        failures.append({"ticker":"*","reason":_SOURCE_BLOCKED_REASON,"detail":f"preflight_ticker={first}; affected_tickers={len(unique)}; further_calls_skipped=true"})
        failures.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("reason",""))))
        return observations,failures

    target_blocked=any(str(row.get("reason") or "")==_TARGET_AUTH_DENIED_REASON for row in first_failures)
    if target_blocked:
        failures.append({"ticker":"*","reason":_TARGET_SOURCE_BLOCKED_REASON,"detail":f"preflight_ticker={first}; target_calls_skipped=true"})
        target_set=set()

    remaining=[ticker for ticker in unique if ticker!=first]
    workers=max(1,min(int(max_workers),len(remaining))) if remaining else 1
    if remaining:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures=[executor.submit(_fetch_one,ticker,api_key,requests,limiter,fetch_target=ticker in target_set) for ticker in remaining]
            for future in as_completed(futures):
                obs,failed=future.result(); observations.extend(obs); failures.extend(failed)
    observations.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("field",""))))
    failures.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("reason",""))))
    return observations,failures


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    if value is None: return None
    text=str(value).strip()
    if not text: return None
    try: parsed=datetime.fromisoformat(text.replace("Z","+00:00"))
    except ValueError: return None
    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_from_timestamp(value: object, now: datetime) -> float:
    fetched=_parse_utc(value)
    if fetched is None: return math.inf
    return max(0.0,(now-fetched).total_seconds()/86400.0)


def _age_days(entry: dict | None, now: datetime) -> float:
    if not entry: return math.inf
    return _age_from_timestamp(entry.get("recommendation_fetched_at_utc") or entry.get("fetched_at_utc"),now)


def _target_age_days(entry: dict | None, now: datetime) -> float:
    if not entry: return math.inf
    return _age_from_timestamp(entry.get("target_fetched_at_utc"),now)


def _normalize_observation(row: dict) -> dict | None:
    if not isinstance(row,dict) or row.get("field") is None or row.get("value") is None: return None
    field=str(row.get("field"))
    return {"field":field,"value":row.get("value"),"group":"TARGET" if field in TARGET_FIELDS else "RECOMMENDATION"}


def _migrate_legacy(payload: dict) -> dict:
    entries=payload.get("entries") if isinstance(payload.get("entries"),dict) else {}
    migrated={"version":CACHE_VERSION,"entries":{}}
    for ticker,entry in entries.items():
        if not isinstance(entry,dict): continue
        fetched=str(entry.get("fetched_at_utc") or "")
        rows=[]; has_target=False
        for raw in entry.get("observations") if isinstance(entry.get("observations"),list) else []:
            normalized=_normalize_observation(raw)
            if normalized is None: continue
            rows.append(normalized)
            has_target=has_target or normalized["group"]=="TARGET"
        migrated["entries"][ticker]={
            "status":entry.get("status","OK"),
            "fetched_at_utc":fetched,
            "recommendation_fetched_at_utc":fetched,
            "target_fetched_at_utc":fetched if has_target else None,
            "target_status":"OK" if has_target else "UNKNOWN",
            "observations":rows,
            "reason":entry.get("reason"),
        }
    migrated["migrated_from"]=LEGACY_CACHE_VERSION
    return migrated


def _load_cache(path: Path) -> dict:
    if not path.exists(): return {"version":CACHE_VERSION,"entries":{}}
    try: payload=json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {"version":CACHE_VERSION,"entries":{}}
    if payload.get("version")==LEGACY_CACHE_VERSION and isinstance(payload.get("entries"),dict):
        return _migrate_legacy(payload)
    if payload.get("version")!=CACHE_VERSION or not isinstance(payload.get("entries"),dict):
        return {"version":CACHE_VERSION,"entries":{}}
    return payload


def _save_cache(path: Path,payload: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    temp.replace(path)


def _entry_observations(
    entry: dict,
    ticker: str,
    *,
    recommendation_live: bool,
    target_live: bool,
) -> list[dict]:
    rows=entry.get("observations") if isinstance(entry.get("observations"),list) else []
    output=[]
    for raw in rows:
        normalized=_normalize_observation(raw)
        if normalized is None: continue
        group=normalized["group"]
        if group=="TARGET":
            fetched_at=str(entry.get("target_fetched_at_utc") or "")
            cache_state="LIVE_REFRESH" if target_live else "CACHE_HIT"
        else:
            fetched_at=str(entry.get("recommendation_fetched_at_utc") or entry.get("fetched_at_utc") or "")
            cache_state="LIVE_REFRESH" if recommendation_live else "CACHE_HIT"
        output.append({"ticker":ticker,"field":normalized["field"],"value":normalized["value"],"source":"Finnhub","fetched_at_utc":fetched_at,"cache_state":cache_state,"field_group":group})
    return output


def _split_rows(rows: list[dict]) -> tuple[list[dict],list[dict]]:
    recommendation=[]; target=[]
    for row in rows:
        normalized=_normalize_observation(row)
        if normalized is None: continue
        (target if normalized["group"]=="TARGET" else recommendation).append(normalized)
    return recommendation,target


def fetch_consensus_cached(
    tickers: list[str],
    api_key: str,
    cache_path: str | Path,
    *,
    refresh_budget: int = 400,
    max_cache_age_days: float = 35.0,
    negative_cache_days: float = 7.0,
    recommendation_ttl_days: float | None = None,
    target_ttl_days: float | None = None,
    target_refresh_budget: int | None = None,
    delay_seconds: float = 1.1,
    max_workers: int = 8,
    refresh_due: bool = True,
    now: datetime | None = None,
) -> tuple[list[dict],list[dict],dict]:
    """Return full-universe consensus using independent recommendation/target TTLs.

    Uncached recommendations are bootstrapped exhaustively. Normal runs refresh
    only due recommendations within a bounded budget. Price targets have their
    own longer TTL and their own smaller budget, avoiding an automatic second API
    call for every recommendation refresh. ``refresh_due=False`` retains missing
    and hard-stale recovery while deferring ordinary TTL refreshes. Cache timestamps
    remain field-group specific and stale values are never re-dated.
    """
    unique=sorted({str(t).strip() for t in tickers if str(t).strip()})
    path=Path(cache_path)
    current=(now or _now_utc()).astimezone(timezone.utc)
    payload=_load_cache(path)
    entries: dict[str,dict]=payload["entries"]
    reco_ttl=float(recommendation_ttl_days if recommendation_ttl_days is not None else max_cache_age_days)
    target_ttl=float(target_ttl_days if target_ttl_days is not None else max_cache_age_days)
    reco_budget=max(0,int(refresh_budget))
    target_budget=max(0,int(target_refresh_budget if target_refresh_budget is not None else refresh_budget))
    if not unique:
        return [],[],{"cache_version":CACHE_VERSION,"requested":0,"refresh_due_enabled":bool(refresh_due),"due_refresh_suppressed":0,"live_refresh_requested":0,"target_live_refresh_requested":0,"cache_hit_tickers":0,"negative_cache_hits":0}

    missing=[]; hard_stale=[]; due=[]; negative_fresh=set()
    for ticker in unique:
        entry=entries.get(ticker)
        age=_age_days(entry,current)
        status=str((entry or {}).get("status") or "")
        if entry is None:
            missing.append(ticker); continue
        if status=="NO_DATA" and age<float(negative_cache_days):
            negative_fresh.add(ticker); continue
        if age>float(max_cache_age_days):
            hard_stale.append(ticker); continue
        if age>=reco_ttl:
            due.append((-age,ticker))
    due.sort()
    mandatory=list(dict.fromkeys(missing+hard_stale))
    capacity=max(0,reco_budget-len(mandatory))
    due_selected=[ticker for _,ticker in due[:capacity]] if refresh_due else []
    selected=list(dict.fromkeys(mandatory+due_selected))

    target_missing=[]; target_due=[]
    for ticker in selected:
        entry=entries.get(ticker)
        age=_target_age_days(entry,current)
        target_status=str((entry or {}).get("target_status") or "UNKNOWN")
        if entry is None or (not math.isfinite(age) and target_status!="NO_DATA"):
            target_missing.append(ticker)
        elif age>=target_ttl:
            target_due.append((-age,ticker))
    target_due.sort()
    # Preserve complete initial coverage; normal target refreshes are bounded.
    target_capacity=max(0,target_budget-len(target_missing))
    target_selected=list(dict.fromkeys(target_missing+[ticker for _,ticker in target_due[:target_capacity]]))

    live_observations=[]; live_failures=[]
    if selected:
        live_observations,live_failures=fetch_consensus(
            selected,
            api_key,
            delay_seconds=delay_seconds,
            max_workers=max_workers,
            target_tickers=set(target_selected),
        )
    source_blocked=any(str(row.get("reason") or "")==_SOURCE_BLOCKED_REASON for row in live_failures)
    target_source_blocked=any(str(row.get("reason") or "")==_TARGET_SOURCE_BLOCKED_REASON for row in live_failures)

    obs_by_ticker: dict[str,list[dict]]={}
    for row in live_observations:
        ticker=str(row.get("ticker") or "")
        if ticker: obs_by_ticker.setdefault(ticker,[]).append(row)
    failures_by_ticker: dict[str,list[dict]]={}
    for failure in live_failures:
        ticker=str(failure.get("ticker") or "")
        if ticker and ticker!="*": failures_by_ticker.setdefault(ticker,[]).append(failure)

    refreshed_at=current.isoformat()
    live_success=0; live_no_data=0; target_live_success=0; target_live_no_data=0
    transient_fallbacks=0; expired_after_failure=0; skipped_due_source_block=0
    recommendation_live=set(); target_live=set()
    target_selected_set=set(target_selected)
    for ticker in selected:
        rows=obs_by_ticker.get(ticker,[])
        reco_rows,target_rows=_split_rows(rows)
        ticker_failures=failures_by_ticker.get(ticker,[])
        old=entries.get(ticker)
        old_rows=old.get("observations",[]) if isinstance(old,dict) and isinstance(old.get("observations"),list) else []
        old_reco,old_target=_split_rows(old_rows)
        reasons={str(item.get("reason") or "") for item in ticker_failures}

        if reco_rows:
            entry=dict(old or {})
            entry.update({
                "status":"OK",
                "fetched_at_utc":refreshed_at,
                "recommendation_fetched_at_utc":refreshed_at,
                "observations":reco_rows+old_target,
            })
            entries[ticker]=entry
            live_success+=1; recommendation_live.add(ticker)
        elif reasons and reasons.issubset(_NO_DATA_REASONS):
            entries[ticker]={
                "status":"NO_DATA",
                "fetched_at_utc":refreshed_at,
                "recommendation_fetched_at_utc":refreshed_at,
                "target_fetched_at_utc":old.get("target_fetched_at_utc") if isinstance(old,dict) else None,
                "target_status":old.get("target_status","UNKNOWN") if isinstance(old,dict) else "UNKNOWN",
                "observations":old_target,
                "reason":"|".join(sorted(reasons)),
            }
            live_no_data+=1
        elif source_blocked:
            skipped_due_source_block+=1
            if old and _age_days(old,current)<=float(max_cache_age_days): transient_fallbacks+=1
            elif old: entries.pop(ticker,None); expired_after_failure+=1
        elif old and _age_days(old,current)<=float(max_cache_age_days):
            transient_fallbacks+=1
            live_failures.append({"ticker":ticker,"reason":"LIVE_REFRESH_FAILED_CACHE_FALLBACK_USED","detail":f"cached_age_days={_age_days(old,current):.2f}"})
        else:
            entries.pop(ticker,None); expired_after_failure+=1

        if ticker not in target_selected_set or ticker not in entries:
            continue
        entry=entries[ticker]
        target_failure=any(str(item.get("reason") or "").startswith("FINNHUB_TARGET_") for item in ticker_failures)
        if target_rows:
            current_reco,_=_split_rows(entry.get("observations",[]))
            entry["observations"]=current_reco+target_rows
            entry["target_fetched_at_utc"]=refreshed_at
            entry["target_status"]="OK"
            target_live_success+=1; target_live.add(ticker)
        elif not target_failure and not target_source_blocked:
            current_reco,_=_split_rows(entry.get("observations",[]))
            entry["observations"]=current_reco
            entry["target_fetched_at_utc"]=refreshed_at
            entry["target_status"]="NO_DATA"
            target_live_no_data+=1; target_live.add(ticker)

    payload["version"]=CACHE_VERSION
    payload["updated_at_utc"]=refreshed_at
    payload["policy"]={
        "refresh_budget":reco_budget,
        "recommendation_ttl_days":reco_ttl,
        "target_refresh_budget":target_budget,
        "target_ttl_days":target_ttl,
        "max_cache_age_days":float(max_cache_age_days),
        "negative_cache_days":float(negative_cache_days),
        "bootstrap_uncached_all":True,
        "refresh_due_enabled":bool(refresh_due),
        "stale_after_failure_forbidden_beyond_hard_max":True,
        "auth_or_entitlement_fail_fast":True,
        "target_auth_fail_fast":True,
        "secret_redaction_required":True,
    }
    _save_cache(path,payload)

    observations=[]; cache_hit_tickers=0; negative_cache_hits=0; unusable=0
    for ticker in unique:
        entry=entries.get(ticker)
        if entry is None: unusable+=1; continue
        age=_age_days(entry,current)
        if age>float(max_cache_age_days): unusable+=1; continue
        status=str(entry.get("status") or "")
        if status=="NO_DATA": negative_cache_hits+=1; continue
        if status!="OK": unusable+=1; continue
        rows=_entry_observations(entry,ticker,recommendation_live=ticker in recommendation_live,target_live=ticker in target_live)
        if ticker not in recommendation_live and rows: cache_hit_tickers+=1
        observations.extend(rows)

    ages=sorted(_age_days(entries.get(ticker),current) for ticker in unique if entries.get(ticker) and math.isfinite(_age_days(entries.get(ticker),current)))
    target_ages=sorted(_target_age_days(entries.get(ticker),current) for ticker in unique if entries.get(ticker) and math.isfinite(_target_age_days(entries.get(ticker),current)))
    def _p95(values: list[float]) -> float | None:
        if not values: return None
        index=min(len(values)-1,max(0,math.ceil(len(values)*0.95)-1))
        return round(float(values[index]),3)
    metrics={
        "cache_version":CACHE_VERSION,
        "requested":len(unique),
        "cache_entries":len(entries),
        "mandatory_refresh_count":len(mandatory),
        "due_refresh_count":len(due),
        "due_refresh_suppressed":max(0,min(len(due),capacity)-len(due_selected)),
        "refresh_due_enabled":bool(refresh_due),
        "live_refresh_requested":len(selected),
        "live_refresh_success":live_success,
        "live_no_data":live_no_data,
        "target_live_refresh_requested":len(target_selected),
        "target_live_refresh_success":target_live_success,
        "target_live_no_data":target_live_no_data,
        "target_calls_avoided":max(0,len(selected)-len(target_selected)),
        "cache_hit_tickers":cache_hit_tickers,
        "negative_cache_hits":negative_cache_hits,
        "transient_cache_fallbacks":transient_fallbacks,
        "expired_after_refresh_failure":expired_after_failure,
        "unusable_tickers":unusable,
        "cache_age_p95_days":_p95(ages),
        "target_cache_age_p95_days":_p95(target_ages),
        "max_cache_age_days":float(max_cache_age_days),
        "recommendation_ttl_days":reco_ttl,
        "target_ttl_days":target_ttl,
        "refresh_budget":reco_budget,
        "target_refresh_budget":target_budget,
        "full_universe_preserved":True,
        "cached_timestamp_preserved":True,
        "source_auth_or_entitlement_blocked":source_blocked,
        "target_source_auth_or_entitlement_blocked":target_source_blocked,
        "network_calls_skipped_due_source_block":max(0,skipped_due_source_block-1) if source_blocked else 0,
        "secret_redaction_required":True,
    }
    observations.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("field",""))))
    live_failures.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("reason",""))))
    return observations,live_failures,metrics
