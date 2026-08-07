from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from v182.io.frames import is_missing


@dataclass(frozen=True)
class QualityResult:
    passed: bool
    checks: list[dict]


def _check(name: str, passed: bool, value, threshold, detail: str = "") -> dict:
    return {"check": name, "passed": bool(passed), "value": value, "threshold": threshold, "detail": detail}


def _transport_failure(reason: str) -> bool:
    text = str(reason or "").upper()
    return text in {
        "API_ERROR", "HTTPERROR", "CONNECTIONERROR", "TIMEOUT", "READTIMEOUT",
        "CONNECTTIMEOUT", "SSLError".upper(), "REQUESTEXCEPTION",
    }


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

    for wave_id in ("WAVE_01", "WAVE_02"):
        m = wave_metrics.get(wave_id, {})
        requested = int(m.get("requested", 0) or 0)
        successful = int(m.get("successful", 0) or 0)
        pct = 100.0 if requested == 0 else round(successful / requested * 100, 2)
        checks.append(_check(f"{wave_id.lower()}_ohlcv_success_pct", pct >= q["ohlcv_success_min_pct"], pct, q["ohlcv_success_min_pct"]))

        source_counts = m.get("source_counts", {}) or {}
        source_total = sum(int(v or 0) for v in source_counts.values())
        checks.append(_check(
            f"{wave_id.lower()}_source_accounting",
            source_total == successful,
            source_total,
            successful,
            f"sources={source_counts}",
        ))

        diagnostics = m.get("diagnostics", {}) or {}
        remaining_after_openfigi = int(diagnostics.get("remaining_after_openfigi", 0) or 0)
        key_present = bool(diagnostics.get("marketstack_key_present", False))
        attempted = int(diagnostics.get("marketstack_attempted", 0) or 0)
        market_needed = key_present and remaining_after_openfigi > 0
        checks.append(_check(
            f"{wave_id.lower()}_marketstack_invoked_if_needed",
            (not market_needed) or attempted > 0,
            attempted,
            ">0 when key present and Yahoo/OpenFIGI gaps remain",
            f"remaining_after_openfigi={remaining_after_openfigi}; key_present={key_present}",
        ))

        failures = diagnostics.get("marketstack_failures", []) or []
        transport_failures = sum(_transport_failure(f.get("reason", "")) for f in failures if isinstance(f, dict))
        checks.append(_check(
            f"{wave_id.lower()}_marketstack_not_total_transport_failure",
            attempted == 0 or transport_failures < attempted,
            transport_failures,
            f"<{attempted}" if attempted else "not applicable",
            f"attempted={attempted}; total_failures={len(failures)}",
        ))

        max_symbols = int(cfg.get("marketstack", {}).get("max_symbols_per_run", 3) or 3)
        checks.append(_check(
            f"{wave_id.lower()}_marketstack_quota_cap",
            attempted <= max_symbols,
            attempted,
            max_symbols,
        ))

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

    for wave_id, key in (("WAVE_04", "fundamentals_availability_min_pct"), ("WAVE_05", "consensus_availability_min_pct")):
        threshold = float(q.get(key, 0.0) or 0.0)
        m = wave_metrics.get(wave_id, {})
        available_pct = float(m.get("available_pct", 0.0) or 0.0)
        requested = int(m.get("requested", 0) or 0)
        passed = requested == 0 or available_pct >= threshold
        checks.append(_check(f"{wave_id.lower()}_availability_pct", passed, available_pct, threshold,
                             f"available={m.get('available', 0)}/{requested}"))

    return QualityResult(all(c["passed"] for c in checks), checks)
