from __future__ import annotations
from pathlib import Path
import os

from v182.sources.yfinance_bulk import download_history, DownloadResult
from v182.mapping.etf_isin_resolver import fallback_specs, expected_mic
from v182.sources.marketstack_eod import fetch_eod_history, save_marketstack_cache
from v182.sources.marketstack_symbols import resolve_marketstack_symbols
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
    marketstack_symbol_cache_path: str | Path | None = None,
    marketstack_eod_budget: int | None = None,
    marketstack_resolution_budget: int | None = None,
) -> DownloadResult:
    """Primary Yahoo OHLCV → OpenFIGI repair → Marketstack symbol resolution/EOD.

    Marketstack budgets may be supplied by the run orchestrator so the Actions
    and ETF waves share one global quota rather than each consuming a full cap.
    """
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
        "openfigi_repair_attempted": 0,
        "remaining_after_openfigi": 0,
        "marketstack_symbol_resolution_attempted": 0,
        "marketstack_symbol_resolution_successful": 0,
        "marketstack_symbol_cache_hits": 0,
        "marketstack_symbol_negative_cache_hits": 0,
        "marketstack_symbol_deferred": 0,
        "marketstack_symbol_failures": [],
        "marketstack_attempted": 0,
        "marketstack_failures": [],
        "marketstack_key_present": bool(os.environ.get("MARKETSTACK_API_KEY")),
        "marketstack_eod_budget": marketstack_eod_budget,
        "marketstack_resolution_budget": marketstack_resolution_budget,
    }

    specs = fallback_specs(valid, sorted(remaining), openfigi_map_path, universe)
    diagnostics["openfigi_specs"] = len(specs)

    candidate_to_original = {}
    for original, spec in specs.items():
        candidate = str(spec.get("yahoo_candidate") or "").strip()
        if candidate and candidate != original and candidate not in candidate_to_original:
            candidate_to_original[candidate] = original
    diagnostics["openfigi_repair_attempted"] = len(candidate_to_original)
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

    diagnostics["remaining_after_openfigi"] = len(remaining)
    market_key = os.environ.get("MARKETSTACK_API_KEY")
    ms_cfg = cfg.get("marketstack", {})
    if remaining and market_key and ms_cfg.get("enabled", True):
        priority = _priority_failed_rows(valid, remaining, universe)
        symbol_cache = Path(marketstack_symbol_cache_path or Path(openfigi_map_path).with_name("V18.2_MARKETSTACK_SYMBOL_MAP.csv"))
        configured_resolution = int(ms_cfg.get("max_new_symbol_resolutions_per_run", 1) or 1)
        resolution_budget = configured_resolution if marketstack_resolution_budget is None else max(0, int(marketstack_resolution_budget))
        symbol_result = resolve_marketstack_symbols(
            priority,
            universe,
            symbol_cache,
            market_key,
            max_new_resolutions=resolution_budget,
            min_confidence=float(ms_cfg.get("symbol_min_confidence", 0.72) or 0.72),
            resolved_ttl_days=int(ms_cfg.get("symbol_resolved_ttl_days", 90) or 90),
            negative_ttl_days=int(ms_cfg.get("symbol_negative_ttl_days", 30) or 30),
            delay_seconds=float(ms_cfg.get("delay_seconds", 0.25) or 0.25),
        )
        diagnostics["marketstack_symbol_resolution_attempted"] = symbol_result.api_attempted
        diagnostics["marketstack_symbol_resolution_successful"] = symbol_result.api_successful
        diagnostics["marketstack_symbol_cache_hits"] = symbol_result.cache_hits
        diagnostics["marketstack_symbol_negative_cache_hits"] = symbol_result.negative_cache_hits
        diagnostics["marketstack_symbol_deferred"] = symbol_result.deferred
        diagnostics["marketstack_symbol_failures"] = symbol_result.failures

        configured_eod = int(os.environ.get("MARKETSTACK_MAX_SYMBOLS_PER_RUN") or ms_cfg.get("max_symbols_per_run", 3))
        max_eod = configured_eod if marketstack_eod_budget is None else max(0, int(marketstack_eod_budget))
        requests_spec = []
        for _, row in priority.iterrows():
            original = str(row.get("yahoo_ticker") or "").strip()
            symbol = symbol_result.resolved.get(original, "")
            mic = expected_mic(original, str(row.get("isin") or ""))
            if not symbol or not mic:
                continue
            requests_spec.append({
                "canonical_ticker": original,
                "symbol": symbol,
                "expected_mic": mic,
            })
            if len(requests_spec) >= max_eod:
                break

        ms = fetch_eod_history(
            requests_spec=requests_spec,
            api_key=market_key,
            history_days=int(ms_cfg.get("history_days", 365)),
            max_symbols=max_eod,
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
