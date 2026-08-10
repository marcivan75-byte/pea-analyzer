from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import os

import pandas as pd

from v182.sources.finnhub_consensus import FINNHUB_BASE, _get_json, fetch_consensus

from .core import CaptureStore, is_observed, utcnow


ROOT = Path(__file__).resolve().parents[3]
BROKER_WEIGHTS = ROOT / "config/V18.2_BROKER_WEIGHTS.csv"

METRIC_MAPPING = {
    "per_ttm_v21": ["peTTM", "peBasicExclExtraTTM", "peNormalizedAnnual"],
    "pb_v21": ["pbTTM", "pbQuarterly", "pbAnnual"],
    "roe_v21_pct": ["roeTTM", "roeRfy", "roeAnnual"],
    "roa_v21_pct": ["roaTTM", "roaRfy", "roaAnnual"],
    "roic_v21_pct": ["roiTTM", "roiAnnual", "returnOnInvestmentTTM", "roicTTM"],
    "operating_margin_v21_pct": ["operatingMarginTTM", "operatingMarginAnnual"],
    "net_margin_v21_pct": ["netProfitMarginTTM", "netProfitMarginAnnual"],
    "revenue_growth_v21_pct": ["revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy"],
    "revenue_cagr_5y_v21_pct": ["revenueGrowth5Y", "revenueGrowth5YAnnual"],
    "earnings_growth_v21_pct": ["epsGrowthTTMYoy", "epsGrowthQuarterlyYoy", "epsGrowth5Y"],
    "ev_to_ebitda_v21": ["ev/ebitdaTTM", "enterpriseValue/ebitdaTTM", "evEbitdaTTM"],
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
        "target_mean_v21", "target_low_v21", "target_high_v21", "n_analysts_v21",
        "consensus_score_100_v21", "consensus_delta_4w", "net_upgrades_30d_v21",
        "per_ttm_v21", "pb_v21", "roe_v21_pct", "roa_v21_pct", "roic_v21_pct",
        "operating_margin_v21_pct", "revenue_growth_v21_pct", "revenue_cagr_5y_v21_pct",
        "earnings_growth_v21_pct", "ev_to_ebitda_v21", "debt_to_ebitda_v21",
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


def _score5(rec: dict) -> tuple[float | None, int]:
    weights = {"strongBuy": 5, "buy": 4, "hold": 3, "sell": 2, "strongSell": 1}
    counts = {key: max(0, int(rec.get(key) or 0)) for key in weights}
    total = sum(counts.values())
    if total <= 0:
        return None, 0
    return sum(counts[k] * weights[k] for k in weights) / total, total


def _score100(score5: float | None) -> float | None:
    return None if score5 is None else round((score5 - 1.0) / 4.0 * 100.0, 4)


def _grade_rank(text: str | None) -> int | None:
    value = str(text or "").upper().replace("-", " ").replace("_", " ")
    if any(k in value for k in ["STRONG BUY", "OUTPERFORM", "OVERWEIGHT", "BUY"]):
        return 4
    if any(k in value for k in ["HOLD", "NEUTRAL", "MARKET PERFORM", "EQUAL WEIGHT"]):
        return 3
    if any(k in value for k in ["UNDERPERFORM", "UNDERWEIGHT", "REDUCE"]):
        return 2
    if "SELL" in value:
        return 1
    return None


def _broker_weight_map() -> dict[str, float]:
    if not BROKER_WEIGHTS.exists():
        return {}
    try:
        data = pd.read_csv(BROKER_WEIGHTS, sep=";", dtype=str, encoding="utf-8-sig")
    except Exception:
        return {}
    if not {"broker", "weight"}.issubset(data.columns):
        return {}
    out: dict[str, float] = {}
    for _, row in data.iterrows():
        name = str(row.get("broker") or "").strip().casefold()
        weight = _number(row.get("weight"))
        if name and weight is not None and weight > 0:
            out[name] = weight
    return out


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

    symbol_cache = store.root / "V21.1_FINNHUB_SYMBOL_MAP.csv"
    observations, failures = fetch_consensus(
        securities,
        token,
        symbol_cache_path=symbol_cache,
        delay_seconds=float(os.getenv("V211_FINNHUB_DELAY_SECONDS", "1.05")),
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
            facts.append(_fact(isin, "consensus_score_100_v21", _score100(score5), today, "FINNHUB_RECOMMENDATION"))
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

    import requests
    session = requests.Session()
    metric_attempted = metric_success = history_attempted = target_attempted = 0
    candidate_rows = {str(row.get("isin") or ""): row for _, row in candidates.iterrows()}

    for isin, symbol in list(symbol_by_isin.items()):
        row = candidate_rows.get(isin, pd.Series(dtype=object))
        try:
            if not is_observed(row.get("consensus_delta_4w")):
                history_attempted += 1
                reco = _get_json(session, "/stock/recommendation", {"symbol": symbol, "token": token}, max_retries=1) or []
                if isinstance(reco, list) and len(reco) >= 2:
                    current5, current_n = _score5(reco[0] or {})
                    previous5, previous_n = _score5(reco[1] or {})
                    if current5 is not None and previous5 is not None and current_n > 0 and previous_n > 0:
                        current100, previous100 = _score100(current5), _score100(previous5)
                        if current100 is not None and previous100 is not None:
                            facts.append(_fact(isin, "consensus_delta_4w", round(current100 - previous100, 4), today, "FINNHUB_RECOMMENDATION_HISTORY"))
                            facts.append(_fact(isin, "consensus_score_100_4w_ago_v21", previous100, today, "FINNHUB_RECOMMENDATION_HISTORY"))
        except Exception as exc:
            failures.append({"isin": isin, "finnhub_symbol": symbol, "stage": "recommendation_history", "reason": type(exc).__name__})

        if any(not is_observed(row.get(field)) for field in ["target_low_v21", "target_high_v21"]):
            target_attempted += 1
            try:
                target = _get_json(session, "/stock/price-target", {"symbol": symbol, "token": token}, max_retries=1) or {}
                for src, dst in {
                    "targetMean": "target_mean_v21",
                    "targetLow": "target_low_v21",
                    "targetHigh": "target_high_v21",
                    "targetMedian": "target_median_v21",
                    "numberAnalysts": "n_analysts_v21",
                }.items():
                    value = _number(target.get(src)) if isinstance(target, dict) else None
                    if value is not None:
                        facts.append(_fact(isin, dst, value, today, "FINNHUB_PRICE_TARGET", 0.87))
            except Exception as exc:
                failures.append({"isin": isin, "finnhub_symbol": symbol, "stage": "price_target", "reason": type(exc).__name__})

        if metric_attempted < int(metric_max):
            metric_attempted += 1
            try:
                payload = _get_json(
                    session,
                    "/stock/metric",
                    {"symbol": symbol, "metric": "all", "token": token},
                    max_retries=1,
                ) or {}
                metric = payload.get("metric", {}) if isinstance(payload, dict) else {}
                if isinstance(metric, dict) and metric:
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

    upgrades_observed = 0
    try:
        events = _get_json(
            session,
            "/stock/upgrade-downgrade",
            {"from": (date.today() - timedelta(days=30)).isoformat(), "to": date.today().isoformat(), "token": token},
            max_retries=1,
        ) or []
        symbol_to_isin = {symbol: isin for isin, symbol in symbol_by_isin.items()}
        grouped: dict[str, list[dict]] = {}
        for event in events if isinstance(events, list) else []:
            symbol = str(event.get("symbol") or "").strip()
            if symbol in symbol_to_isin:
                grouped.setdefault(symbol, []).append(event)
        broker_weights = _broker_weight_map()
        for symbol, rows in grouped.items():
            isin = symbol_to_isin[symbol]
            ups = sum(1 for item in rows if str(item.get("action") or "").lower() == "up")
            downs = sum(1 for item in rows if str(item.get("action") or "").lower() == "down")
            if ups + downs:
                upgrades_observed += 1
                facts.extend([
                    _fact(isin, "upgrades_30d_v21", ups, today, "FINNHUB_UPGRADE_DOWNGRADE", 0.88),
                    _fact(isin, "downgrades_30d_v21", downs, today, "FINNHUB_UPGRADE_DOWNGRADE", 0.88),
                    _fact(isin, "net_upgrades_30d_v21", ups - downs, today, "FINNHUB_UPGRADE_DOWNGRADE", 0.88),
                ])
            weighted = total_weight = 0.0
            for item in rows:
                old_rank = _grade_rank(item.get("fromGrade"))
                new_rank = _grade_rank(item.get("toGrade"))
                if old_rank is None or new_rank is None:
                    continue
                broker = str(item.get("company") or "").strip().casefold()
                weight = broker_weights.get(broker, 1.0)
                weighted += (new_rank - old_rank) * 25.0 * weight
                total_weight += weight
            if total_weight:
                facts.append(_fact(isin, "broker_weighted_revision_30d", round(weighted / total_weight, 4), today, "FINNHUB_UPGRADE_DOWNGRADE_BROKER_WEIGHTED", 0.88))
    except Exception as exc:
        failures.append({"stage": "upgrade_downgrade_bulk", "reason": f"{type(exc).__name__}:{str(exc)[:120]}"})

    added = store.upsert_facts(facts)
    status = "OK" if added else ("NO_NEW_DATA" if not failures else "PARTIAL")
    store.add_health(
        "FINNHUB_FREE",
        status,
        attempted=len(securities),
        succeeded=len(by_isin),
        failed=len(failures),
        message=(
            f"facts={added}; metric_attempted={metric_attempted}; metric_success={metric_success}; "
            f"recommendation_history={history_attempted}; target_calls={target_attempted}; upgrades_symbols={upgrades_observed}"
        ),
    )
    return {
        "status": status,
        "attempted": len(securities),
        "resolved_with_observation": len(by_isin),
        "failures": len(failures),
        "metric_attempted": metric_attempted,
        "metric_success": metric_success,
        "recommendation_history_attempted": history_attempted,
        "price_target_attempted": target_attempted,
        "upgrade_downgrade_symbols": upgrades_observed,
        "facts_added": added,
        "endpoint": FINNHUB_BASE,
        "documented_fields_covered": [
            "ROE", "ROA", "ROIC_IF_EXPOSED", "PER", "EV_EBITDA_IF_EXPOSED", "REVENUE_CAGR_5Y_IF_EXPOSED",
            "FCF_YIELD", "TARGETS", "CONSENSUS", "UPGRADES_DOWNGRADES_30D", "BROKER_WEIGHTED_REVISION"
        ],
    }
