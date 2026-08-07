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

    for wave_id, key in (("WAVE_04", "fundamentals_availability_min_pct"), ("WAVE_05", "consensus_availability_min_pct")):
        threshold = float(q.get(key, 0.0) or 0.0)
        m = wave_metrics.get(wave_id, {})
        available_pct = float(m.get("available_pct", 0.0) or 0.0)
        requested = int(m.get("requested", 0) or 0)
        passed = requested == 0 or available_pct >= threshold
        checks.append(_check(f"{wave_id.lower()}_availability_pct", passed, available_pct, threshold,
                             f"available={m.get('available', 0)}/{requested}"))

    return QualityResult(all(c["passed"] for c in checks), checks)
