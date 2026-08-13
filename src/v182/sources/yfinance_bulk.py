from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import os
import time

import pandas as pd

logger = logging.getLogger(__name__)
PRICE_FIELDS={"open","high","low","close","adj close"}
CACHE_FORMAT_VERSION=2
DEFAULT_INCREMENTAL_PERIOD="1mo"


@dataclass(frozen=True)
class DownloadResult:
    requested: int
    successful: list[str]
    failed: list[str]
    cache_file: str | None


def _has_observed_price(frame) -> bool:
    if frame is None or frame.empty:
        return False
    columns=list(frame.columns)
    price_columns=[c for c in columns if str(c).strip().lower() in PRICE_FIELDS]
    if not price_columns:
        return False
    return bool(frame[price_columns].notna().to_numpy().any())


def _contains_ticker(frame, ticker: str) -> bool:
    """A ticker is successful only when at least one real OHLC price is present."""
    if frame is None or frame.empty:
        return False
    if isinstance(frame.columns,pd.MultiIndex):
        for level in range(frame.columns.nlevels):
            if ticker in frame.columns.get_level_values(level):
                try:
                    sub=frame.xs(ticker,axis=1,level=level,drop_level=True)
                except (KeyError,ValueError):
                    return False
                return _has_observed_price(sub)
        return False
    return _has_observed_price(frame)


def _clear_history_cache(cache:Path)->None:
    """Explicit full-rebuild helper; normal daily runs never call it on a valid cache."""
    targets=list(cache.glob("history_*.parquet"))
    manifest=cache/"history_manifest.json"
    if manifest.exists(): targets.append(manifest)
    for path in targets:
        try:
            path.unlink()
        except OSError as exc:
            raise RuntimeError(f"YFINANCE_CACHE_CLEANUP_FAILED:{path.name}:{type(exc).__name__}") from exc


def _resolve_actions_requested(cache_dir: str, include_actions: bool | None) -> bool:
    if include_actions is not None:
        return bool(include_actions)
    return Path(cache_dir).name.lower() == "actions"


def _normalize_frame(frame:pd.DataFrame | None,tickers:list[str])->pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out=frame.copy()
    if isinstance(out.columns,pd.MultiIndex):
        ticker_level=None
        for level in range(out.columns.nlevels):
            values=set(str(v) for v in out.columns.get_level_values(level))
            if any(t in values for t in tickers):
                ticker_level=level
                break
        if ticker_level not in (None,0):
            order=[ticker_level]+[i for i in range(out.columns.nlevels) if i!=ticker_level]
            out.columns=out.columns.reorder_levels(order)
    elif len(tickers)==1:
        out.columns=pd.MultiIndex.from_tuples([(tickers[0],str(c)) for c in out.columns])
    if isinstance(out.index,pd.DatetimeIndex) and out.index.tz is not None:
        out.index=out.index.tz_localize(None)
    return out.sort_index()


def _merge_history_frames(existing:pd.DataFrame | None,update:pd.DataFrame | None)->pd.DataFrame:
    """Append recent observations while letting the newest download replace overlap."""
    if existing is None or existing.empty:
        return pd.DataFrame() if update is None else update.sort_index()
    if update is None or update.empty:
        return existing.sort_index()
    merged=pd.concat([existing,update],axis=0,sort=False)
    merged=merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()


def _read_manifest(cache:Path)->dict | None:
    path=cache/"history_manifest.json"
    if not path.exists():
        return None
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return None
    return payload if isinstance(payload,dict) else None


def _cache_is_usable(cache:Path,tickers:list[str],interval:str,batch_size:int,auto_adjust:bool,actions_requested:bool)->bool:
    manifest=_read_manifest(cache)
    if not manifest or manifest.get("cache_format_version")!=CACHE_FORMAT_VERSION:
        return False
    if manifest.get("requested_tickers")!=tickers:
        return False
    if manifest.get("interval")!=interval or int(manifest.get("batch_size",-1))!=int(batch_size):
        return False
    if bool(manifest.get("auto_adjust"))!=bool(auto_adjust) or bool(manifest.get("actions_requested"))!=bool(actions_requested):
        return False
    return all((cache/f"history_{start:05d}.parquet").exists() for start in range(0,len(tickers),batch_size))


def _updated_today_utc(manifest:dict | None)->bool:
    raw=(manifest or {}).get("updated_at_utc")
    if not raw:
        return False
    try:
        stamp=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp=stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).date()==datetime.now(timezone.utc).date()


def _read_cached_frame(path:Path,tickers:list[str])->pd.DataFrame:
    try:
        return _normalize_frame(pd.read_parquet(path),tickers)
    except Exception as exc:
        logger.warning("OHLCV cache read failed for %s: %s: %s",path,type(exc).__name__,exc)
        return pd.DataFrame()


def _download_one(yf,ticker:str,period:str,interval:str,auto_adjust:bool,actions_requested:bool):
    frame=yf.download(
        tickers=[ticker],period=period,interval=interval,group_by="ticker",
        auto_adjust=auto_adjust,actions=actions_requested,threads=False,progress=False,timeout=30,
    )
    frame=_normalize_frame(frame,[ticker])
    return frame if _contains_ticker(frame,ticker) else pd.DataFrame()


def download_history(
    tickers: list[str],
    cache_dir: str,
    period: str = "5y",
    interval: str = "1d",
    batch_size: int = 100,
    auto_adjust: bool = True,
    include_actions: bool | None = None,
) -> DownloadResult:
    """Maintain a persistent OHLCV history with incremental daily refreshes.

    First use (or an invalid/incompatible cache) bootstraps the configured full
    history, normally 5y. Subsequent runs download only a recent overlap window
    (1mo by default), merge it into the retained parquet history and never erase
    valid long history. A second run on the same UTC day reuses the current cache.

    Set PEA_YF_FORCE_REFRESH=1 to refresh again on the same day, or
    PEA_YF_FORCE_FULL_HISTORY=1 for an explicit full reconstruction.
    """
    import yfinance as yf

    clean=sorted({t.strip() for t in tickers if t and t.strip()})
    cache=Path(cache_dir); cache.mkdir(parents=True,exist_ok=True)
    actions_requested=_resolve_actions_requested(cache_dir,include_actions)
    force_full=os.environ.get("PEA_YF_FORCE_FULL_HISTORY","").strip()=="1"
    force_refresh=os.environ.get("PEA_YF_FORCE_REFRESH","").strip()=="1"
    incremental_period=os.environ.get("PEA_YF_INCREMENTAL_PERIOD",DEFAULT_INCREMENTAL_PERIOD).strip() or DEFAULT_INCREMENTAL_PERIOD
    usable=(not force_full) and _cache_is_usable(cache,clean,interval,batch_size,auto_adjust,actions_requested)
    prior_manifest=_read_manifest(cache) if usable else None

    if usable and not force_refresh and _updated_today_utc(prior_manifest):
        successful=sorted(set(prior_manifest.get("successful",[])))
        failed=sorted(set(prior_manifest.get("failed",[])))
        logger.info("OHLCV cache hit: already refreshed today (%s tickers)",len(clean))
        return DownloadResult(len(clean),successful,failed,str(cache/"history_manifest.json"))

    mode="INCREMENTAL" if usable else "FULL_BOOTSTRAP"
    if not usable:
        _clear_history_cache(cache)

    successful:list[str]=[]; failed:list[str]=[]; cached_tickers:set[str]=set(); failure_details:list[dict]=[]
    for start in range(0,len(clean),batch_size):
        batch=clean[start:start+batch_size]
        output=cache/f"history_{start:05d}.parquet"
        existing=_read_cached_frame(output,batch) if mode=="INCREMENTAL" else pd.DataFrame()
        request_period=incremental_period if not existing.empty else period
        fresh=pd.DataFrame()
        missing=list(batch)
        try:
            raw=yf.download(
                tickers=batch,period=request_period,interval=interval,group_by="ticker",
                auto_adjust=auto_adjust,actions=actions_requested,threads=True,progress=False,timeout=30,
            )
            fresh=_normalize_frame(raw,batch)
            missing=[]
            for ticker in batch:
                if _contains_ticker(fresh,ticker): successful.append(ticker)
                else: missing.append(ticker)
        except Exception as exc:
            logger.warning("yfinance batch %s failed: %s: %s; individual retry",start,type(exc).__name__,exc)
            failure_details.append({"scope":"batch","batch_start":start,"error":type(exc).__name__,"detail":str(exc)[:180]})

        for ticker in missing:
            try:
                retry=_download_one(yf,ticker,request_period,interval,auto_adjust,actions_requested)
                if retry.empty:
                    failed.append(ticker)
                    failure_details.append({"scope":"ticker","ticker":ticker,"error":"EMPTY_OR_NO_PRICE_DATA"})
                else:
                    successful.append(ticker)
                    fresh=_merge_history_frames(fresh,retry)
            except Exception as exc:
                failed.append(ticker)
                failure_details.append({"scope":"ticker","ticker":ticker,"error":type(exc).__name__,"detail":str(exc)[:180]})
                logger.debug("yfinance ticker retry failed for %s: %s: %s",ticker,type(exc).__name__,exc)
            time.sleep(0.12)

        combined=_merge_history_frames(existing,fresh)
        if not combined.empty:
            try:
                combined.to_parquet(output)
            except Exception as exc:
                logger.warning("OHLCV cache write failed for %s: %s: %s",output,type(exc).__name__,exc)
                failure_details.append({"scope":"cache_write","batch_start":start,"error":type(exc).__name__,"detail":str(exc)[:180]})
            for ticker in batch:
                if _contains_ticker(combined,ticker): cached_tickers.add(ticker)
        time.sleep(0.25)

    successful=sorted(set(successful)); failed=sorted(set(failed)-set(successful))
    manifest=cache/"history_manifest.json"
    payload={
        "cache_format_version":CACHE_FORMAT_VERSION,
        "mode":mode,
        "updated_at_utc":datetime.now(timezone.utc).isoformat(),
        "requested":len(clean),
        "requested_tickers":clean,
        "cached_tickers":sorted(cached_tickers),
        "successful":successful,
        "failed":failed,
        "failure_details":failure_details,
        "actions_requested":actions_requested,
        "interval":interval,
        "batch_size":int(batch_size),
        "auto_adjust":bool(auto_adjust),
        "bootstrap_period":period,
        "incremental_period":incremental_period,
    }
    try:
        manifest.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception as exc:
        logger.error("OHLCV manifest write failed for %s: %s: %s",manifest,type(exc).__name__,exc)
        return DownloadResult(len(clean),successful,failed,None)
    logger.info("OHLCV %s: %s/%s refreshed, %s cached",mode,len(successful),len(clean),len(cached_tickers))
    return DownloadResult(len(clean),successful,failed,str(manifest))


def _retry_individual(yf, tickers, cache: Path, batch_start: int, period: str, interval: str, auto_adjust: bool, actions_requested: bool, successful: list[str], failed: list[str], failure_details: list[dict]) -> None:
    """Backward-compatible retry helper retained for external tests/callers."""
    output=cache/f"history_{batch_start:05d}.parquet"
    existing=_read_cached_frame(output,list(tickers))
    fresh=pd.DataFrame()
    for ticker in tickers:
        try:
            retry=_download_one(yf,ticker,period,interval,auto_adjust,actions_requested)
            if retry.empty:
                failed.append(ticker); failure_details.append({"scope":"ticker","ticker":ticker,"error":"EMPTY_OR_NO_PRICE_DATA"})
            else:
                successful.append(ticker); fresh=_merge_history_frames(fresh,retry)
        except Exception as exc:
            failed.append(ticker); failure_details.append({"scope":"ticker","ticker":ticker,"error":type(exc).__name__,"detail":str(exc)[:180]})
        time.sleep(0.12)
    combined=_merge_history_frames(existing,fresh)
    if not combined.empty:
        combined.to_parquet(output)
