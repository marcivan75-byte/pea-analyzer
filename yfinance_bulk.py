from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import time

@dataclass(frozen=True)
class DownloadResult:
    requested: int
    successful: list[str]
    failed: list[str]
    cache_file: str | None

def download_history(
    tickers: list[str],
    cache_dir: str,
    period: str = "5y",
    interval: str = "1d",
    batch_size: int = 100,
    auto_adjust: bool = True,
) -> DownloadResult:
    """Download OHLCV in batches. A failed ticker never aborts the whole run."""
    import yfinance as yf  # import différé : yfinance n'est requis qu'au moment de l'appel réseau
    clean = sorted({t.strip() for t in tickers if t and t.strip()})
    successful: list[str] = []
    failed: list[str] = []
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    for start in range(0, len(clean), batch_size):
        batch = clean[start:start + batch_size]
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
            output = cache / f"history_{start:05d}.parquet"
            frame.to_parquet(output)
            # yfinance can return partial data; test each ticker in the columns.
            for ticker in batch:
                found = ticker in frame.columns.get_level_values(0) if hasattr(frame.columns, "levels") else not frame.empty
                (successful if found else failed).append(ticker)
        except Exception:
            failed.extend(batch)
        time.sleep(0.5)

    manifest = cache / "history_manifest.json"
    manifest.write_text(json.dumps({
        "requested": len(clean),
        "successful": successful,
        "failed": failed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return DownloadResult(len(clean), successful, failed, str(manifest))
