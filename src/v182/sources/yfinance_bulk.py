from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json
import time


@dataclass(frozen=True)
class DownloadResult:
    requested: int
    successful: list[str]
    failed: list[str]
    cache_file: str | None
    source_counts: dict[str, int] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)


def _ensure_multiindex(frame, batch: list[str]):
    """Normalize a one-symbol yfinance frame to the project MultiIndex format."""
    if frame is None or frame.empty or hasattr(frame.columns, "levels"):
        return frame
    if len(batch) != 1:
        return frame
    import pandas as pd
    return pd.concat({batch[0]: frame}, axis=1)


def _ticker_has_data(frame, ticker: str, min_rows: int = 20) -> bool:
    """A ticker is successful only if it contains usable close prices.

    yfinance may create MultiIndex columns for failed symbols filled entirely
    with NaN; merely finding the symbol in columns is therefore not enough.
    """
    import pandas as pd

    if frame is None or frame.empty:
        return False
    try:
        if hasattr(frame.columns, "levels"):
            if ticker not in frame.columns.get_level_values(0):
                return False
            sub = frame[ticker]
        else:
            sub = frame
        close = sub["Close"] if "Close" in sub.columns else None
        if close is None:
            return False
        usable = pd.to_numeric(close, errors="coerce").dropna()
        return len(usable) >= int(min_rows)
    except Exception:
        return False


def _rename_top_level(frame, canonical_map: dict[str, str] | None):
    if not canonical_map or frame is None or frame.empty or not hasattr(frame.columns, "levels"):
        return frame
    renamed = frame.copy()
    renamed.columns = type(frame.columns).from_tuples(
        [(canonical_map.get(str(a), str(a)), b) for a, b in frame.columns.to_list()],
        names=frame.columns.names,
    )
    return renamed


def download_history(
    tickers: list[str],
    cache_dir: str,
    period: str = "5y",
    interval: str = "1d",
    batch_size: int = 100,
    auto_adjust: bool = True,
    retry_count: int = 0,
    retry_backoff_seconds: float = 30.0,
    retry_batch_size: int = 25,
    batch_delay_seconds: float = 0.5,
    min_rows: int = 20,
    file_prefix: str = "history",
    canonical_map: dict[str, str] | None = None,
    threads: bool = True,
    serial_rescue_attempts: int = 0,
    serial_rescue_backoff_seconds: float = 90.0,
    serial_rescue_batch_size: int = 5,
    serial_rescue_batch_delay_seconds: float = 1.5,
) -> DownloadResult:
    """Download OHLCV with validation, bounded retries and serial rescue.

    The normal path keeps yfinance batching for speed. If Yahoo starts rate
    limiting a large bulk request, the optional rescue path waits, reduces the
    batch size and disables yfinance multithreading. This is intentionally done
    before consuming scarce paid/API fallbacks for what may only be a transient
    Yahoo throttle.

    ``canonical_map`` maps fetched symbols to the canonical symbols used by the
    rest of V18.2. This lets OpenFIGI repair a Yahoo symbol while keeping the
    original ISIN/ticker relationship for downstream indicators.
    """
    import yfinance as yf

    clean = sorted({str(t).strip() for t in tickers if str(t or "").strip()})
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    successful: set[str] = set()
    failed_fetch: set[str] = set()
    diagnostics = {
        "threaded_primary": bool(threads),
        "retry_rounds_configured": max(0, int(retry_count)),
        "serial_rescue_rounds_configured": max(0, int(serial_rescue_attempts)),
        "serial_rescue_attempted_symbols": 0,
        "serial_rescue_successful_symbols": 0,
    }

    def fetch_batches(
        symbols: list[str],
        effective_batch: int,
        attempt_label: str,
        use_threads: bool,
        delay_seconds: float,
    ):
        nonlocal successful, failed_fetch
        for start in range(0, len(symbols), effective_batch):
            batch = symbols[start:start + effective_batch]
            try:
                frame = yf.download(
                    tickers=batch,
                    period=period,
                    interval=interval,
                    group_by="ticker",
                    auto_adjust=auto_adjust,
                    threads=use_threads,
                    progress=False,
                    timeout=30,
                )
                frame = _ensure_multiindex(frame, batch)
                good_fetch, bad_fetch = [], []
                for ticker in batch:
                    (good_fetch if _ticker_has_data(frame, ticker, min_rows=min_rows) else bad_fetch).append(ticker)
                canonical_frame = _rename_top_level(frame, canonical_map)
                output = cache / f"{file_prefix}_{attempt_label}_{start:05d}.parquet"
                if canonical_frame is not None and not canonical_frame.empty:
                    canonical_frame.to_parquet(output)
                for ticker in good_fetch:
                    successful.add((canonical_map or {}).get(ticker, ticker))
                    failed_fetch.discard(ticker)
                for ticker in bad_fetch:
                    failed_fetch.add(ticker)
            except Exception:
                failed_fetch.update(batch)
            time.sleep(max(0.0, float(delay_seconds)))

    fetch_batches(
        clean,
        max(1, int(batch_size)),
        "primary",
        bool(threads),
        batch_delay_seconds,
    )

    for attempt in range(1, max(0, int(retry_count)) + 1):
        retry_symbols = sorted(failed_fetch)
        if not retry_symbols:
            break
        time.sleep(max(0.0, float(retry_backoff_seconds)) * attempt)
        failed_fetch = set()
        fetch_batches(
            retry_symbols,
            max(1, min(int(retry_batch_size), int(batch_size))),
            f"retry{attempt}",
            bool(threads),
            batch_delay_seconds,
        )

    # Final recovery for transient Yahoo throttling. At this point only failed
    # fetched symbols remain. The rescue is deliberately slower and serial, and
    # it never changes identity or treats an empty response as success.
    rescue_success_before = len(successful)
    rescue_attempted: set[str] = set()
    for attempt in range(1, max(0, int(serial_rescue_attempts)) + 1):
        rescue_symbols = sorted(failed_fetch)
        if not rescue_symbols:
            break
        rescue_attempted.update(rescue_symbols)
        time.sleep(max(0.0, float(serial_rescue_backoff_seconds)) * attempt)
        failed_fetch = set()
        fetch_batches(
            rescue_symbols,
            max(1, min(int(serial_rescue_batch_size), int(batch_size))),
            f"serial_rescue{attempt}",
            False,
            serial_rescue_batch_delay_seconds,
        )

    diagnostics["serial_rescue_attempted_symbols"] = len(rescue_attempted)
    diagnostics["serial_rescue_successful_symbols"] = max(0, len(successful) - rescue_success_before)

    failed_canonical = sorted({(canonical_map or {}).get(t, t) for t in failed_fetch} - successful)
    manifest = cache / f"{file_prefix}_manifest.json"
    manifest.write_text(json.dumps({
        "requested": len(clean),
        "successful": sorted(successful),
        "failed": failed_canonical,
        "diagnostics": diagnostics,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return DownloadResult(
        len(clean), sorted(successful), failed_canonical, str(manifest),
        source_counts={"yfinance": len(successful)}, diagnostics=diagnostics,
    )
