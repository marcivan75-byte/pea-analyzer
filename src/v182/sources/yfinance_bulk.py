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
) -> DownloadResult:
    """Download OHLCV in batches with real data validation and bounded retry.

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

    def fetch_batches(symbols: list[str], effective_batch: int, attempt_label: str):
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
                    threads=True,
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
            time.sleep(max(0.0, float(batch_delay_seconds)))

    fetch_batches(clean, max(1, int(batch_size)), "primary")

    for attempt in range(1, max(0, int(retry_count)) + 1):
        retry_symbols = sorted(failed_fetch)
        if not retry_symbols:
            break
        time.sleep(max(0.0, float(retry_backoff_seconds)) * attempt)
        failed_fetch = set()
        fetch_batches(retry_symbols, max(1, min(int(retry_batch_size), int(batch_size))), f"retry{attempt}")

    failed_canonical = sorted({(canonical_map or {}).get(t, t) for t in failed_fetch} - successful)
    manifest = cache / f"{file_prefix}_manifest.json"
    manifest.write_text(json.dumps({
        "requested": len(clean),
        "successful": sorted(successful),
        "failed": failed_canonical,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return DownloadResult(
        len(clean), sorted(successful), failed_canonical, str(manifest),
        source_counts={"yfinance": len(successful)}, diagnostics={},
    )
