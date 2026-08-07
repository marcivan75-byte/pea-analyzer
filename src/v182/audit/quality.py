from __future__ import annotations
from dataclasses import dataclass
import os
import pandas as pd
from v182.io.frames import is_missing


@dataclass(frozen=True)
class QualityResult:
    passed: bool
    checks: list[dict]


def _check(name: str, passed: bool, value, threshold, detail: str = "") -> dict:
    return {"check": name, "passed": bool(passed), "value": value, "threshold": threshold, "detail": detail}


def _transport_failure(reason: str) -> bool:
    return str(reason or "").upper() in {
        "API_ERROR", "HTTPERROR", "CONNECTIONERROR", "TIMEOUT", "READTIMEOUT",
        "CONNECTTIMEOUT", "SSLERROR", "REQUESTEXCEPTION",
    }


def _alpha_runtime_failure(item: dict) -> bool:
    reason = str(item.get("reason") or "").upper()
    detail = str(item.get("detail") or "").upper()
    text = f"{reason} {detail}"
    return any(token in text for token in (
        "ALPHA_VANTAGE_API_ERROR", "ALPHA_VANTAGE_API_KEY_MISSING",
        "HTTPERROR", "CONNECTIONERROR", "TIMEOUT", "READTIMEOUT",
        "CONNECTTIMEOUT", "SSLERROR", "REQUESTEXCEPTION",
    ))


def _active_api_runtime_check(checks: list[dict], name: str, metrics: dict, enabled: bool) -> None:
    key_present = bool(metrics.get("key_present", False))
    error = str(metrics.get("error") or "")
    success = bool(metrics.get("success", False))
    passed = (not enabled) or (not key_present) or (success and not error)
    checks.append(_check(
        f"{name}_runtime_health_if_key_present",
        passed,
        "OK" if success and not error else (error or "SKIPPED_NO_KEY" if not key_present else "FAILED"),
        "OK when enabled and key present",
        f"enabled={enabled}; key_present={key_present}",
    ))


def run_quality_gates(actions: pd.DataFrame, etf: pd.DataFrame, before: dict, after: dict, cfg: dict, wave_metrics: dict) -> QualityResult:
    q = cfg["quality_gates"]
    checks = []
    checks.append(_check("actions_row_count", len(actions) >= q["actions_min_rows"], len(actions), q["actions_min_rows"]))
    checks.append(_check("etf_row_count", len(etf) >= q["etf_min_rows"], len(etf), q["etf_min_rows"]))
    checks.append(_check("actions_unique_isin", actions["isin"].nunique() == len(actions), actions["isin"].nunique(), len(actions)))
    checks.append(_check("etf_unique_isin", etf["isin"].nunique() == len(etf), etf["isin"].nunique(), len(etf)))

    for universe, frame in (("actions", actions), ("etf", etf)):
        ticker_pct = round((~frame["yahoo_ticker"].apply(is_missing)).mean() * 100, 2)
        checks.append(_check(f"{universe}_ticker_coverage_pct", ticker_pct >= q["ticker_coverage_min_pct"], ticker_pct, q["ticker_coverage_min_pct"]))

    tol = q["coverage_regression_tolerance_points"]
    for key in ("ACTION", "ETF"):
        delta = after[key]["coverage_pct"] - before[key]["coverage_pct"]
        checks.append(_check(f"{key.lower()}_coverage_no_regression", delta >= -tol, round(delta, 2), f">=-{tol}"))

    aggregate_eod = 0
    aggregate_resolution = 0
    aggregate_alpha_calls = 0
    aggregate_alpha_securities = 0
    alpha_failures: list[dict] = []

    for wave_id in ("WAVE_01", "WAVE_02"):
        m = wave_metrics.get(wave_id, {})
        requested = int(m.get("requested", 0) or 0)
        successful = int(m.get("successful", 0) or 0)
        pct = 100.0 if requested == 0 else round(successful / requested * 100, 2)
        checks.append(_check(f"{wave_id.lower()}_ohlcv_success_pct", pct >= q["ohlcv_success_min_pct"], pct, q["ohlcv_success_min_pct"]))

        source_counts = m.get("source_counts") or {}
        if source_counts:
            source_total = sum(int(v or 0) for v in source_counts.values())
            checks.append(_check(f"{wave_id.lower()}_source_accounting", source_total == successful, source_total, successful, f"sources={source_counts}"))

        diagnostics = m.get("diagnostics") or {}
        remaining_after_openfigi = int(diagnostics.get("remaining_after_openfigi", 0) or 0)
        key_present = bool(diagnostics.get("marketstack_key_present", False))
        eod_attempted = int(diagnostics.get("marketstack_attempted", 0) or 0)
        resolver_attempted = int(diagnostics.get("marketstack_symbol_resolution_attempted", 0) or 0)
        aggregate_eod += eod_attempted
        aggregate_resolution += resolver_attempted
        cache_hits = int(diagnostics.get("marketstack_symbol_cache_hits", 0) or 0)
        negative_cache_hits = int(diagnostics.get("marketstack_symbol_negative_cache_hits", 0) or 0)
        deferred = int(diagnostics.get("marketstack_symbol_deferred", 0) or 0)
        budget_exhausted = bool(diagnostics.get("marketstack_budget_exhausted", False))
        market_needed = key_present and remaining_after_openfigi > 0
        activity = eod_attempted + resolver_attempted + cache_hits + negative_cache_hits + deferred + int(budget_exhausted)
        checks.append(_check(
            f"{wave_id.lower()}_marketstack_path_invoked_if_needed",
            (not market_needed) or activity > 0,
            activity,
            ">0 when key present and Yahoo/OpenFIGI gaps remain",
            f"remaining_after_openfigi={remaining_after_openfigi}; key_present={key_present}; resolver={resolver_attempted}; cache={cache_hits}; negative_cache={negative_cache_hits}; deferred={deferred}; budget_exhausted={budget_exhausted}; eod={eod_attempted}",
        ))

        symbol_failures = diagnostics.get("marketstack_symbol_failures", []) or []
        resolver_transport = sum(_transport_failure(f.get("reason", "")) for f in symbol_failures if isinstance(f, dict) and not f.get("cached"))
        checks.append(_check(
            f"{wave_id.lower()}_marketstack_resolver_transport_health",
            resolver_attempted == 0 or resolver_transport < resolver_attempted,
            resolver_transport,
            f"<{resolver_attempted}" if resolver_attempted else "not applicable",
        ))

        failures = diagnostics.get("marketstack_failures", []) or []
        transport_failures = sum(_transport_failure(f.get("reason", "")) for f in failures if isinstance(f, dict))
        checks.append(_check(
            f"{wave_id.lower()}_marketstack_eod_transport_health",
            eod_attempted == 0 or transport_failures < eod_attempted,
            transport_failures,
            f"<{eod_attempted}" if eod_attempted else "not applicable",
        ))

        aggregate_alpha_calls += int(diagnostics.get("alpha_resolution_api_calls", 0) or 0)
        aggregate_alpha_calls += int(diagnostics.get("alpha_history_api_calls", 0) or 0)
        aggregate_alpha_securities += int(diagnostics.get("alpha_security_attempted", 0) or 0)
        alpha_failures.extend([f for f in (diagnostics.get("alpha_failures", []) or []) if isinstance(f, dict)])

    market_cfg = cfg.get("marketstack", {})
    configured_eod = int(market_cfg.get("max_symbols_per_run", 3) or 3)
    try:
        eod_cap = int(os.environ.get("MARKETSTACK_MAX_SYMBOLS_PER_RUN") or configured_eod)
    except ValueError:
        eod_cap = configured_eod
    resolution_cap = int(market_cfg.get("max_new_symbol_resolutions_per_run", 1) or 1)
    checks.append(_check("marketstack_global_eod_quota_cap", aggregate_eod <= max(0, eod_cap), aggregate_eod, max(0, eod_cap)))
    checks.append(_check("marketstack_global_resolver_quota_cap", aggregate_resolution <= max(0, resolution_cap), aggregate_resolution, max(0, resolution_cap)))

    openfigi = wave_metrics.get("WAVE_00_OPENFIGI", {}) or {}
    api_requested = int(openfigi.get("api_isins_requested", 0) or 0)
    transient = int(openfigi.get("transient_failures", 0) or 0)
    authenticated = bool(openfigi.get("authenticated", False))
    transient_pct = 0.0 if api_requested == 0 else round(transient / api_requested * 100, 2)
    max_transient_pct = float(q.get("openfigi_transient_failure_max_pct", 25.0) or 25.0)
    checks.append(_check(
        "openfigi_transport_health",
        api_requested == 0 or not authenticated or transient_pct <= max_transient_pct,
        transient_pct,
        max_transient_pct,
        f"api_requested={api_requested}; transient_failures={transient}; authenticated={authenticated}",
    ))

    alpha_cfg = cfg.get("alpha_vantage", {})
    alpha_security_cap = max(0, int(alpha_cfg.get("max_securities_per_run", 1) or 1))
    alpha_call_cap = alpha_security_cap * max(1, int(alpha_cfg.get("max_api_calls_per_selected_security", 4) or 4))
    checks.append(_check("alpha_vantage_security_quota_cap", aggregate_alpha_securities <= alpha_security_cap,
                         aggregate_alpha_securities, alpha_security_cap))
    checks.append(_check("alpha_vantage_api_quota_cap", aggregate_alpha_calls <= alpha_call_cap,
                         aggregate_alpha_calls, alpha_call_cap))
    runtime_alpha_failures = [f for f in alpha_failures if _alpha_runtime_failure(f)]
    checks.append(_check(
        "alpha_vantage_runtime_health_if_invoked",
        aggregate_alpha_securities == 0 or not runtime_alpha_failures,
        len(runtime_alpha_failures),
        0,
        f"securities_attempted={aggregate_alpha_securities}; total_failures={len(alpha_failures)}",
    ))

    if q.get("fail_active_api_on_runtime_error", True):
        _active_api_runtime_check(checks, "fred", wave_metrics.get("WAVE_MACRO_FRED", {}) or {}, bool(cfg.get("fred", {}).get("enabled", False)))
        _active_api_runtime_check(checks, "eia", wave_metrics.get("WAVE_ENERGY_EIA", {}) or {}, bool(cfg.get("eia", {}).get("enabled", False)))

    for wave_id, key in (("WAVE_04", "fundamentals_availability_min_pct"), ("WAVE_05", "consensus_availability_min_pct")):
        threshold = float(q.get(key, 0.0) or 0.0)
        m = wave_metrics.get(wave_id, {})
        available_pct = float(m.get("available_pct", 0.0) or 0.0)
        requested = int(m.get("requested", 0) or 0)
        passed = requested == 0 or available_pct >= threshold
        checks.append(_check(f"{wave_id.lower()}_availability_pct", passed, available_pct, threshold,
                             f"available={m.get('available', 0)}/{requested}"))

    return QualityResult(all(c["passed"] for c in checks), checks)
