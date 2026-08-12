from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import logging
import time

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadResult:
    requested: int
    successful: list[str]
    failed: list[str]
    cache_file: str | None


def _contains_ticker(frame, ticker: str) -> bool:
    if frame is None or frame.empty:
        return False
    if hasattr(frame.columns, "levels"):
        return ticker in frame.columns.get_level_values(0)
    return True


def download_history(
    tickers: list[str],
    cache_dir: str,
    period: str = "5y",
    interval: str = "1d",
    batch_size: int = 100,
    auto_adjust: bool = True,
    include_actions: bool | None = None,
) -> DownloadResult:
    """Download market history with observable retries/cache failures.

    Corporate actions are intentionally requested only for the Action cache so
    Action LT can derive dividend continuity/CAGR. ETF ACC/DIST policy belongs to
    the ETF structural referential and must not be overwritten by observed cash
    dividends from price history. Callers may override with `include_actions`.
    """
    import yfinance as yf

    clean = sorted({t.strip() for t in tickers if t and t.strip()})
    successful: list[str] = []
    failed: list[str] = []
    failure_details: list[dict] = []
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    actions_requested=(cache.name.lower()=="actions") if include_actions is None else bool(include_actions)

    for start in range(0, len(clean), batch_size):
        batch = clean[start:start + batch_size]
        try:
            frame = yf.download(
                tickers=batch, period=period, interval=interval, group_by="ticker",
                auto_adjust=auto_adjust, actions=actions_requested, threads=True, progress=False, timeout=30,
            )
            output = cache / f"history_{start:05d}.parquet"
            try:
                frame.to_parquet(output)
            except Exception as exc:
                logger.warning("OHLCV cache write failed for %s: %s: %s", output, type(exc).__name__, exc)
                failure_details.append({"scope":"cache_write","batch_start":start,"error":type(exc).__name__,"detail":str(exc)[:180]})
            missing=[]
            for ticker in batch:
                if _contains_ticker(frame,ticker): successful.append(ticker)
                else: missing.append(ticker)
            if missing:
                logger.info("yfinance partial batch %s: %s/%s tickers missing; individual retry", start, len(missing), len(batch))
                _retry_individual(yf, missing, cache, start, period, interval, auto_adjust, actions_requested, successful, failed, failure_details)
        except Exception as exc:
            logger.warning("yfinance batch %s failed: %s: %s; individual retry", start, type(exc).__name__, exc)
            failure_details.append({"scope":"batch","batch_start":start,"error":type(exc).__name__,"detail":str(exc)[:180]})
            _retry_individual(yf, batch, cache, start, period, interval, auto_adjust, actions_requested, successful, failed, failure_details)
        time.sleep(0.5)

    successful=sorted(set(successful))
    failed=sorted(set(failed)-set(successful))
    manifest = cache / "history_manifest.json"
    payload={"requested":len(clean),"successful":successful,"failed":failed,"failure_details":failure_details,"actions_requested":actions_requested}
    try:
        manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("OHLCV manifest write failed for %s: %s: %s", manifest, type(exc).__name__, exc)
        return DownloadResult(len(clean), successful, failed, None)
    return DownloadResult(len(clean), successful, failed, str(manifest))


def _retry_individual(yf, tickers, cache: Path, batch_start: int, period: str, interval: str, auto_adjust: bool, actions_requested: bool, successful: list[str], failed: list[str], failure_details: list[dict]) -> None:
    for offset,ticker in enumerate(tickers):
        try:
            frame=yf.download(tickers=[ticker],period=period,interval=interval,group_by="ticker",auto_adjust=auto_adjust,actions=actions_requested,threads=False,progress=False,timeout=30)
            if _contains_ticker(frame,ticker):
                successful.append(ticker)
                try:
                    frame.to_parquet(cache/f"history_retry_{batch_start:05d}_{offset:03d}.parquet")
                except Exception as exc:
                    logger.debug("Retry cache write failed for %s: %s: %s",ticker,type(exc).__name__,exc)
                    failure_details.append({"scope":"retry_cache","ticker":ticker,"error":type(exc).__name__,"detail":str(exc)[:180]})
            else:
                failed.append(ticker)
                failure_details.append({"scope":"ticker","ticker":ticker,"error":"EMPTY_OR_PARTIAL"})
        except Exception as exc:
            failed.append(ticker)
            logger.debug("yfinance ticker retry failed for %s: %s: %s",ticker,type(exc).__name__,exc)
            failure_details.append({"scope":"ticker","ticker":ticker,"error":type(exc).__name__,"detail":str(exc)[:180]})
        time.sleep(0.12)
