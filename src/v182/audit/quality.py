from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from v182.io.frames import is_missing

IDENTITY_ONLY_STATUS="WHITELIST_ONLY_MISSING_METADATA"


@dataclass(frozen=True)
class QualityResult:
    passed: bool
    checks: list[dict]


def _check(name: str, passed: bool, value, threshold, detail: str="") -> dict:
    return {"check": name, "passed": bool(passed), "value": value, "threshold": threshold, "detail": detail}


def _ticker_gate(frame: pd.DataFrame, universe: str, threshold: float) -> list[dict]:
    ticker_missing=frame["yahoo_ticker"].apply(is_missing)
    if universe!="actions" or "canonical_seed_status" not in frame.columns:
        pct=round((~ticker_missing).mean()*100,2)
        return [_check(f"{universe}_ticker_coverage_pct",pct>=threshold,pct,threshold)]

    identity_only=frame["canonical_seed_status"].astype(str).eq(IDENTITY_ONLY_STATUS)
    eligible=~identity_only
    eligible_count=int(eligible.sum())
    eligible_pct=100.0 if eligible_count==0 else round((~ticker_missing[eligible]).mean()*100,2)
    accounted=((~ticker_missing)|identity_only)
    accounted_pct=round(accounted.mean()*100,2)
    identity_only_with_ticker=int((identity_only & ~ticker_missing).sum())
    return [
        _check(
            "actions_ticker_coverage_eligible_pct",
            eligible_pct>=threshold,
            eligible_pct,
            threshold,
            f"Ticker coverage is strict on {eligible_count} market-data-eligible rows; whitelist-only identity skeletons are tracked separately.",
        ),
        _check(
            "actions_missing_ticker_explicitly_identity_only_pct",
            accounted_pct>=100.0,
            accounted_pct,
            100.0,
            "Every Action missing a ticker must be explicitly tagged WHITELIST_ONLY_MISSING_METADATA.",
        ),
        _check(
            "actions_identity_only_rows_have_no_invented_ticker",
            identity_only_with_ticker==0,
            identity_only_with_ticker,
            0,
            "Identity-only rows must not acquire an invented ticker without an explicit hydration/status transition.",
        ),
    ]


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
    checks.extend(_ticker_gate(actions,"actions",float(q["ticker_coverage_min_pct"])))
    checks.extend(_ticker_gate(etf,"etf",float(q["ticker_coverage_min_pct"])))
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
