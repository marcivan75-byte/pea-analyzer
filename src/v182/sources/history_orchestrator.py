from __future__ import annotations
from pathlib import Path
import os

from v182.sources.yfinance_bulk import download_history, DownloadResult
from v182.mapping.etf_isin_resolver import fallback_specs
from v182.sources.marketstack_eod import fetch_eod_history, save_marketstack_cache
from v182.io.frames import is_missing


def _priority_failed_rows(df, failed: set[str], universe: str):
    rows = df[df["yahoo_ticker"].astype(str).isin(failed)].copy()
    if rows.empty:
        return rows
    if universe == "ACTION":
        if "comite_status" in rows.columns:
            rows["_committee"] = rows["comite_status"].astype(str).isin(["COMMITTEE", "WATCH"]).astype(int)
        else:
            rows["_committee"] = 0
        if "score_brut" in rows.columns:
            import pandas as pd
            rows["_score"] = pd.to_numeric(rows["score_brut"], errors="coerce").fillna(-1e9)
        else:
            rows["_score"] = -1e9
        rows = rows.sort_values(["_committee", "_score"], ascending=[False, False])
    return rows


def download_history_with_fallback(
    df,
    universe: str,
    cache_dir: str,
    cfg: dict,
    openfigi_map_path: str | Path,
) -> DownloadResult:
    """Primary Yahoo OHLCV plus OpenFIGI symbol repair and Marketstack fallback."""
    valid = df[df["yahoo_ticker"].apply(lambda v: not is_missing(v))]
    batch_key = "actions_batch_size" if universe == "ACTION" else "etf_batch_size"
    yf_cfg = cfg.get("yfinance", {})
    primary = download_history(
        tickers=valid["yahoo_ticker"].tolist(),
        cache_dir=cache_dir,
        period=yf_cfg.get("history_period", "5y"),
        interval=yf_cfg.get("interval", "1d"),
        batch_size=int(yf_cfg.get(batch_key, 100)),
        auto_adjust=bool(yf_cfg.get("auto_adjust", True)),
        retry_count=int(yf_cfg.get("history_retry_count", 2)),
        retry_backoff_seconds=float(yf_cfg.get("history_retry_backoff_seconds", 30)),
        retry_batch_size=int(yf_cfg.get("history_retry_batch_size", 25)),
        batch_delay_seconds=float(yf_cfg.get("history_batch_delay_seconds", 0.8)),
        min_rows=int(yf_cfg.get("history_min_rows", 20)),
        file_prefix="history_yahoo",
    )

    successful = set(primary.successful)
    remaining = set(primary.failed)
    source_counts = {"yfinance": len(successful), "yfinance_openfigi_repair": 0, "marketstack": 0}
    diagnostics = {
        "yahoo_failed_initial": len(remaining),
        "openfigi_specs": 0,
        "marketstack_attempted": 0,
        "marketstack_failures": [],
        "marketstack_key_present": bool(os.environ.get("MARKETSTACK_API_KEY")),
    }

    specs = fallback_specs(valid, sorted(remaining), openfigi_map_path, universe)
    diagnostics["openfigi_specs"] = len(specs)

    candidate_to_original = {}
    for original, spec in specs.items():
        candidate = str(spec.get("yahoo_candidate") or "").strip()
        if candidate and candidate != original and candidate not in candidate_to_original:
            candidate_to_original[candidate] = original
    if candidate_to_original:
        repair = download_history(
            tickers=list(candidate_to_original),
            cache_dir=cache_dir,
            period=yf_cfg.get("history_period", "5y"),
            interval=yf_cfg.get("interval", "1d"),
            batch_size=min(25, len(candidate_to_original)),
            auto_adjust=bool(yf_cfg.get("auto_adjust", True)),
            retry_count=1,
            retry_backoff_seconds=float(yf_cfg.get("history_retry_backoff_seconds", 30)),
            retry_batch_size=10,
            batch_delay_seconds=float(yf_cfg.get("history_batch_delay_seconds", 0.8)),
            min_rows=int(yf_cfg.get("history_min_rows", 20)),
            file_prefix="history_openfigi_repair",
            canonical_map=candidate_to_original,
        )
        repaired = set(repair.successful)
        successful |= repaired
        remaining -= repaired
        source_counts["yfinance_openfigi_repair"] = len(repaired)

    market_key = os.environ.get("MARKETSTACK_API_KEY")
    ms_cfg = cfg.get("marketstack", {})
    if remaining and market_key and ms_cfg.get("enabled", True):
        priority = _priority_failed_rows(valid, remaining, universe)
        requests_spec = []
        for _, row in priority.iterrows():
            original = str(row.get("yahoo_ticker") or "").strip()
            spec = specs.get(original, {})
            symbol = str(spec.get("marketstack_symbol") or "").strip()
            if not symbol:
                continue
            requests_spec.append({
                "canonical_ticker": original,
                "symbol": symbol,
                "expected_mic": str(spec.get("marketstack_mic") or "").strip(),
            })
        ms = fetch_eod_history(
            requests_spec=requests_spec,
            api_key=market_key,
            history_days=int(ms_cfg.get("history_days", 365)),
            max_symbols=int(os.environ.get("MARKETSTACK_MAX_SYMBOLS_PER_RUN") or ms_cfg.get("max_symbols_per_run", 4)),
            auto_adjust=bool(yf_cfg.get("auto_adjust", True)),
            min_rows=int(ms_cfg.get("min_rows", 60)),
            delay_seconds=float(ms_cfg.get("delay_seconds", 0.25)),
        )
        save_marketstack_cache(ms.frames, cache_dir)
        market_success = set(ms.frames)
        successful |= market_success
        remaining -= market_success
        source_counts["marketstack"] = len(market_success)
        diagnostics["marketstack_attempted"] = ms.attempted
        diagnostics["marketstack_failures"] = ms.failures

    diagnostics["failed_final"] = len(remaining)
    return DownloadResult(
        requested=primary.requested,
        successful=sorted(successful),
        failed=sorted(remaining),
        cache_file=primary.cache_file,
        source_counts=source_counts,
        diagnostics=diagnostics,
    )
