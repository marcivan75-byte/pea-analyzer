from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from v182.io.frames import is_missing

@dataclass(frozen=True)
class QualityResult:
    passed: bool
    checks: list[dict]

def _check(name: str, passed: bool, value, threshold, detail: str="") -> dict:
    return {"check": name, "passed": bool(passed), "value": value, "threshold": threshold, "detail": detail}

def run_quality_gates(actions: pd.DataFrame, etf: pd.DataFrame, before: dict, after: dict, cfg: dict, wave_metrics: dict, expected_rows: dict | None = None) -> QualityResult:
    q=cfg["quality_gates"]
    checks=[]
    expected_rows = expected_rows or {}
    actions_min = int(expected_rows.get("ACTION", q["actions_min_rows"]))
    etf_min = int(expected_rows.get("ETF", q["etf_min_rows"]))
    checks.append(_check("actions_row_count_no_universe_loss", len(actions)>=actions_min, len(actions), actions_min,
                         "Expected row count is frozen from the canonical input loaded at run start."))
    checks.append(_check("etf_row_count_no_universe_loss", len(etf)>=etf_min, len(etf), etf_min,
                         "Expected row count is frozen from the canonical input loaded at run start."))
    checks.append(_check("actions_unique_isin", actions["isin"].nunique()==len(actions), actions["isin"].nunique(), len(actions)))
    checks.append(_check("etf_unique_isin", etf["isin"].nunique()==len(etf), etf["isin"].nunique(), len(etf)))
    for universe, frame in (("actions",actions),("etf",etf)):
        ticker_pct=round((~frame["yahoo_ticker"].apply(is_missing)).mean()*100,2)
        checks.append(_check(f"{universe}_ticker_coverage_pct", ticker_pct>=q["ticker_coverage_min_pct"], ticker_pct, q["ticker_coverage_min_pct"]))
    tol=q["coverage_regression_tolerance_points"]
    for key in ("ACTION","ETF"):
        delta=after[key]["coverage_pct"]-before[key]["coverage_pct"]
        checks.append(_check(f"{key.lower()}_coverage_no_regression", delta>=-tol, round(delta,2), f">=-{tol}"))
    for wave_id in ("WAVE_01","WAVE_02"):
        m=wave_metrics.get(wave_id,{})
        requested=int(m.get("requested",0) or 0); successful=int(m.get("successful",0) or 0)
        pct=100.0 if requested==0 else round(successful/requested*100,2)
        checks.append(_check(f"{wave_id.lower()}_ohlcv_success_pct", pct>=q["ohlcv_success_min_pct"], pct, q["ohlcv_success_min_pct"]))
    return QualityResult(all(c["passed"] for c in checks), checks)
