from __future__ import annotations

from datetime import date
from pathlib import Path
import os
import time

import pandas as pd

from v182.sources.finnhub_consensus import FINNHUB_BASE, _get_json, fetch_consensus

from .core import CaptureStore, is_observed, utcnow


METRIC_MAPPING = {
    "pb_v21": ["pbTTM", "pbQuarterly", "pbAnnual"],
    "roe_v21_pct": ["roeTTM", "roeRfy", "roeAnnual"],
    "roa_v21_pct": ["roaTTM", "roaRfy", "roaAnnual"],
    "operating_margin_v21_pct": ["operatingMarginTTM", "operatingMarginAnnual"],
    "net_margin_v21_pct": ["netProfitMarginTTM", "netProfitMarginAnnual"],
    "revenue_growth_v21_pct": ["revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy", "revenueGrowth5Y"],
    "earnings_growth_v21_pct": ["epsGrowthTTMYoy", "epsGrowthQuarterlyYoy", "epsGrowth5Y"],
    "debt_to_equity_v21": ["totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual"],
    "debt_to_ebitda_v21": ["totalDebt/ebitdaTTM", "netDebt/ebitdaTTM"],
    "current_ratio_v21": ["currentRatioQuarterly", "currentRatioAnnual"],
    "interest_coverage_v21": ["interestCoverageTTM", "interestCoverageAnnual"],
    "dividend_yield_v21_pct": ["currentDividendYieldTTM", "dividendYieldIndicatedAnnual"],
    "beta_v21": ["beta"],
    "high_52w": ["52WeekHigh"],
    "low_52w": ["52WeekLow"],
    "fcf_yield_v21": ["freeCashFlowYieldTTM", "fcfYieldTTM"],
}


def _number(value: object) -> float | None:
    try:
        value = float(value)
        return value if pd.notna(value) else None
    except (TypeError, ValueError):
        return None


def _metric(metric: dict, names: list[str]) -> float | None:
    for name in names:
        value = _number(metric.get(name))
        if value is not None:
            return value
    return None


def _needs_capture(row: pd.Series) -> bool:
    watched = [
        "target_mean_v21", "n_analysts_v21", "consensus_score_100_v21",
        "pb_v21", "roe_v21_pct", "roa_v21_pct", "operating_margin_v21_pct",
        "revenue_growth_v21_pct", "earnings_growth_v21_pct", "debt_to_ebitda_v21",
        "current_ratio_v21", "interest_coverage_v21", "fcf_yield_v21",
    ]
    return any(field not in row.index or not is_observed(row.get(field)) for field in watched)


def _fact(isin: str, field: str, value: object, as_of: str, evidence: str, confidence: float = 0.86) -> dict:
    numeric = _number(value)
    return {
        "isin": isin,
        "field": field,
        "value": numeric if numeric is not None else "",
        "value_text": "" if numeric is not None else str(value or ""),
        "as_of": as_of,
        "source": "FINNHUB_FREE",
        "evidence": evidence,
        "confidence": confidence,
        "status": "OBSERVED_FREE",
        "observed_at_utc": utcnow(),
    }


def capture(prioritized: pd.DataFrame, store: CaptureStore, max_symbols: int = 120, metric_max: int = 60) -> dict:
    token = str(os.getenv("FINNHUB_API_KEY") or "").strip()
    if not token:
        store.add_health("FINNHUB_FREE", "SKIPPED_NO_KEY", message="FINNHUB_API_KEY missing")
        return {"status": "SKIPPED_NO_KEY", "attempted": 0, "facts_added": 0}

    candidates = prioritized.loc[prioritized.apply(_needs_capture, axis=1)].head(max(0, int(max_symbols))).copy()
    if candidates.empty:
        store.add_health("FINNHUB_FREE", "NO_MISSING_FIELD")
        return {"status": "NO_MISSING_FIELD", "attempted": 0, "facts_added": 0}

    securities = []
    for _, row in candidates.iterrows():
        securities.append({
            "isin": str(row.get("isin") or "").strip(),
            "yahoo_ticker": str(row.get("yahoo_ticker") or row.get("ticker_yahoo_final") or "").strip(),
            "name": str(row.get("name") or "").strip(),
        })

    delay_seconds = max(1.05, float(os.getenv("V211_FINNHUB_DELAY_SECONDS", "1.05")))
    symbol_cache = store.root / "V21.1_FINNHUB_SYMBOL_MAP.csv"
    observations, failures = fetch_consensus(
        securities,
        token,
        symbol_cache_path=symbol_cache,
        delay_seconds=delay_seconds,
        max_retries=2,
    )

    today = date.today().isoformat()
    facts: list[dict] = []
    by_isin: dict[str, dict[str, object]] = {}
    symbol_by_isin: dict[str, str] = {}
    for obs in observations:
        isin = str(obs.get("isin") or "").strip()
        if not isin:
            continue
        by_isin.setdefault(isin, {})[str(obs.get("field") or "")] = obs.get("value")
        symbol = str(obs.get("finnhub_symbol") or "").strip()
        if symbol:
            symbol_by_isin[isin] = symbol

    for isin, values in by_isin.items():
        score5 = _number(values.get("consensus_score"))
        if score5 is not None:
            score100 = max(0.0, min(100.0, (score5 - 1.0) / 4.0 * 100.0))
            facts.append(_fact(isin, "consensus_score_100_v21", round(score100, 4), today, "FINNHUB_RECOMMENDATION"))
        mapping = {
            "n_analysts": "n_analysts_v21",
            "target_price": "target_mean_v21",
            "buy_n": "finnhub_buy_n_v21",
            "hold_n": "finnhub_hold_n_v21",
            "sell_n": "finnhub_sell_n_v21",
        }
        for source_field, target_field in mapping.items():
            value = values.get(source_field)
            if value is not None:
                facts.append(_fact(isin, target_field, value, today, "FINNHUB_RECOMMENDATION_OR_TARGET"))

    metric_attempted = 0
    metric_success = 0
    if metric_max > 0 and symbol_by_isin:
        import requests

        session = requests.Session()
        for isin, symbol in list(symbol_by_isin.items())[: int(metric_max)]:
            metric_attempted += 1
            try:
                payload = _get_json(
                    session,
                    "/stock/metric",
                    {"symbol": symbol, "metric": "all", "token": token},
                    max_retries=1,
                ) or {}
                metric = payload.get("metric", {}) if isinstance(payload, dict) else {}
                if not isinstance(metric, dict) or not metric:
                    continue
                observed = 0
                for field, names in METRIC_MAPPING.items():
                    value = _metric(metric, names)
                    if value is None:
                        continue
                    facts.append(_fact(isin, field, value, today, "FINNHUB_BASIC_FINANCIALS", 0.83))
                    observed += 1
                if observed:
                    metric_success += 1
            except Exception as exc:
                failures.append({"isin": isin, "finnhub_symbol": symbol, "stage": "metric", "reason": type(exc).__name__})
            finally:
                time.sleep(delay_seconds)

    added = store.upsert_facts(facts)
    status = "OK" if added else ("NO_NEW_DATA" if not failures else "PARTIAL")
    store.add_health(
        "FINNHUB_FREE",
        status,
        attempted=len(securities),
        succeeded=len(by_isin),
        failed=len(failures),
        message=f"facts={added}; metric_attempted={metric_attempted}; metric_success={metric_success}; delay_seconds={delay_seconds}",
    )
    return {
        "status": status,
        "attempted": len(securities),
        "resolved_with_observation": len(by_isin),
        "failures": len(failures),
        "metric_attempted": metric_attempted,
        "metric_success": metric_success,
        "facts_added": added,
        "delay_seconds": delay_seconds,
        "endpoint": FINNHUB_BASE,
    }
