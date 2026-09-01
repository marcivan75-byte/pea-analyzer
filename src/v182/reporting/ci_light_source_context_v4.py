from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
import json

import pandas as pd

from v182.sources.boursorama_selected_audit73 import collect_selected_action_context_cached
from v182.sources.boursorama_selected_etf import collect_selected_etf_context_cached
from v182.sources.rate_limit import StartRateLimiter
from v182.sources.tradingview_technical import collect_technical_context_cached


CONFIG = Path("config/CI_LIGHT_V4.json")


def _pivot(observations: list[dict]) -> pd.DataFrame:
    if not observations:
        return pd.DataFrame()
    frame = pd.DataFrame(observations)
    index = [field for field in ("isin", "asset_class", "horizon") if field in frame]
    return frame.pivot_table(index=index, columns="field", values="value", aggfunc="last").reset_index()


def _shared_boursorama_fetcher(limiter: StartRateLimiter, max_inflight: int):
    gate = BoundedSemaphore(max(1, int(max_inflight)))

    def fetch(url: str, *, timeout: float):
        import requests

        with gate:
            limiter.wait()
            return requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/CI-Light-V4)"},
                timeout=timeout,
            )

    return fetch


def collect_ci_light_context(rows: pd.DataFrame, root: Path) -> tuple[pd.DataFrame, dict]:
    """Collect CI LIGHT sources for its own bounded universe, without any CI selection."""
    config = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    maximum = int(config["universe"]["maximum_unique_instruments"])
    if rows.empty or "isin" not in rows:
        return rows.copy(), {"status": "NO_CI_LIGHT_UNIVERSE_ROWS"}
    if int(rows["isin"].nunique()) > maximum:
        raise RuntimeError("CI_LIGHT_UNIVERSE_EXCEEDS_GOVERNED_MAXIMUM")

    bcfg = config["boursorama"]
    tcfg = config["tradingview"]
    asset = rows["asset_class"].astype(str).str.upper()
    actions = rows[asset.eq("ACTION")].copy()
    etfs = rows[asset.eq("ETF")].copy()
    limiter = StartRateLimiter(float(bcfg["request_start_interval_seconds"]))
    fetcher = _shared_boursorama_fetcher(limiter, int(bcfg["provider_max_inflight"]))

    def boursorama_branch():
        def collect_actions():
            return collect_selected_action_context_cached(
                actions,
                root / "state/provenance/source_cache/CI_LIGHT_V4_BOURSORAMA_ACTIONS.json",
                dynamic_ttl_hours=float(bcfg["dynamic_ttl_hours"]),
                deep_ttl_hours=float(bcfg["deep_ttl_hours"]),
                refresh_budget=int(bcfg["refresh_budget"]),
                request_start_interval_seconds=0.0,
                max_workers=int(bcfg["provider_max_inflight"]),
                fetcher=fetcher,
            ) if not actions.empty else None

        def collect_etfs():
            return collect_selected_etf_context_cached(
                etfs,
                root / "state/provenance/source_cache/CI_LIGHT_V4_BOURSORAMA_ETFS.json",
                dynamic_ttl_hours=float(bcfg["dynamic_ttl_hours"]),
                deep_ttl_hours=float(bcfg["deep_ttl_hours"]),
                refresh_budget=int(bcfg["refresh_budget"]),
                request_start_interval_seconds=0.0,
                max_workers=int(bcfg["provider_max_inflight"]),
                fetcher=fetcher,
            ) if not etfs.empty else None

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ci-light-boursorama-assets") as pool:
            action_future = pool.submit(collect_actions)
            etf_future = pool.submit(collect_etfs)
            return action_future.result(), etf_future.result()

    def tradingview_branch():
        return collect_technical_context_cached(
            rows,
            root / "state/provenance/source_cache/CI_LIGHT_V4_TRADINGVIEW_V2.json",
            refresh_budget=int(tcfg["refresh_budget"]),
            ttl_hours=float(tcfg["ttl_hours"]),
            request_start_interval_seconds=float(tcfg["request_start_interval_seconds"]),
            max_workers=int(tcfg["provider_max_inflight"]),
        )

    errors: list[dict] = []
    action_result = etf_result = tradingview_result = None
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ci-light-sources") as pool:
        b_future = pool.submit(boursorama_branch)
        tv_future = pool.submit(tradingview_branch)
        try:
            action_result, etf_result = b_future.result()
        except Exception as exc:
            errors.append({"source": "Boursorama", "reason": type(exc).__name__, "detail": str(exc)[:240]})
        try:
            tradingview_result = tv_future.result()
        except Exception as exc:
            errors.append({"source": "TradingView", "reason": type(exc).__name__, "detail": str(exc)[:240]})

    observations: list[dict] = []
    failures: list[dict] = list(errors)
    for result in (action_result, etf_result, tradingview_result):
        if result is not None:
            observations.extend(result.observations)
            failures.extend(result.failures)

    context = _pivot(observations)
    enriched = rows.copy()
    if not context.empty:
        keys = [field for field in ("isin", "asset_class", "horizon") if field in enriched and field in context]
        enriched = enriched.merge(context, on=keys, how="left")

    outdir = root / "outputs/source_context"
    auditdir = root / "outputs/audit"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(observations).to_csv(
        outdir / "CI_LIGHT_V4_INDEPENDENT_SOURCE_OBSERVATIONS.csv",
        sep=";", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame(failures).to_csv(
        outdir / "CI_LIGHT_V4_INDEPENDENT_SOURCE_FAILURES.csv",
        sep=";", index=False, encoding="utf-8-sig",
    )
    payload = {
        "status": "SUCCESS_WITH_CONTEXT" if observations else "SUCCESS_NO_SOURCE_DATA",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_rows": int(len(rows)),
        "unique_instruments": int(rows["isin"].nunique()),
        "universe_source": "DEDICATED_CI_LIGHT_REFERENCE",
        "ci_selection_used": False,
        "ci_context_reused": False,
        "boursorama_actions": action_result.metrics if action_result is not None else {"status": "NO_USABLE_RESULT"},
        "boursorama_etfs": etf_result.metrics if etf_result is not None else {"status": "NO_USABLE_RESULT"},
        "tradingview": tradingview_result.metrics if tradingview_result is not None else {"status": "NO_USABLE_RESULT"},
        "failures": int(len(failures)),
        "investing_enabled": False,
        "source_can_create_ci_light_candidate": True,
        "source_can_create_ci_candidate": False,
        "raw_html_persisted": False,
        "audit73_boursorama_history_enabled": bool(action_result and action_result.metrics.get("audit73_pit_history_enabled")),
        "audit73_boursorama_captures_appended": int(action_result.metrics.get("audit73_captures_appended", 0)) if action_result is not None else 0,
        "audit73_boursorama_rows_appended": int(action_result.metrics.get("audit73_rows_appended", 0)) if action_result is not None else 0,
    }
    (auditdir / "CI_LIGHT_V4_INDEPENDENT_SOURCE_CONTEXT.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return enriched, payload
