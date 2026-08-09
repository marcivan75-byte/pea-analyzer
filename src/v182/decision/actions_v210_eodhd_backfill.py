from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
import csv
import json
import math
import os
import time

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "outputs/V21.0_ACTIONS_PEA_1429_PREPARED.csv"
AUDIT = ROOT / "outputs/audit/V21.0_ACTIONS_EODHD_BACKFILL.json"
CACHE = ROOT / "outputs/cache/V21.0_EODHD_SYMBOL_MAP.csv"
BASE = "https://eodhd.com/api"

MAX_SYMBOLS = int(os.getenv("EODHD_MAX_SYMBOLS_PER_RUN", "1429") or "1429")
WORKERS = max(1, min(12, int(os.getenv("EODHD_WORKERS", "8") or "8")))
REQUESTS_PER_SECOND = max(1.0, float(os.getenv("EODHD_REQUESTS_PER_SECOND", "12") or "12"))
TREND_BATCH_SIZE = max(10, min(100, int(os.getenv("EODHD_TREND_BATCH_SIZE", "75") or "75")))

_CACHE_FIELDS = ["isin", "yahoo_ticker", "eodhd_symbol", "status", "updated_at"]
_rate_lock = Lock()
_last_request = 0.0


def _f(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _pct(value):
    x = _f(value)
    if x is None:
        return None
    return x * 100.0 if abs(x) <= 1.5 else x


def _missing(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip().lower() in {"", "nan", "none", "null", "n/a", "<na>"}


def _fill(df: pd.DataFrame, i: int, field: str, value, source: str = "EODHD") -> int:
    if value is None:
        return 0
    if field not in df.columns:
        df[field] = pd.Series(pd.NA, index=df.index, dtype="object")
    if _missing(df.at[i, field]):
        df.at[i, field] = value
        src = f"src_{field}"
        if src not in df.columns:
            df[src] = pd.Series(pd.NA, index=df.index, dtype="object")
        if _missing(df.at[i, src]):
            df.at[i, src] = source
        return 1
    return 0


def _throttle() -> None:
    global _last_request
    interval = 1.0 / REQUESTS_PER_SECOND
    with _rate_lock:
        now = time.monotonic()
        wait = interval - (now - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _get_json(path: str, token: str, params: dict | None = None, retries: int = 3):
    params = dict(params or {})
    params.update({"api_token": token, "fmt": "json"})
    last = None
    for attempt in range(retries + 1):
        _throttle()
        try:
            r = requests.get(f"{BASE}{path}", params=params, timeout=25)
            if r.status_code == 429:
                last = RuntimeError("EODHD_RATE_LIMIT")
                if attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
                continue
    if last:
        raise last
    return None


def _load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    try:
        with CACHE.open("r", encoding="utf-8-sig", newline="") as fh:
            return {r["isin"]: r for r in csv.DictReader(fh, delimiter=";") if r.get("isin")}
    except Exception:
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_CACHE_FIELDS, delimiter=";")
        w.writeheader()
        for isin in sorted(cache):
            w.writerow({k: cache[isin].get(k, "") for k in _CACHE_FIELDS})


def _cache_fresh(row: dict, positive_days: int = 120, negative_days: int = 14) -> bool:
    ttl = positive_days if str(row.get("status") or "").upper() == "RESOLVED" else negative_days
    try:
        stamp = datetime.fromisoformat(str(row.get("updated_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stamp).days <= ttl
    except Exception:
        return False


def _resolve(isin: str, yahoo_ticker: str, token: str, cache: dict[str, dict], cache_lock: Lock) -> tuple[str | None, str]:
    with cache_lock:
        old = dict(cache.get(isin, {}))
    if old and str(old.get("yahoo_ticker") or "").upper() == yahoo_ticker.upper() and _cache_fresh(old):
        if str(old.get("status") or "").upper() == "RESOLVED" and old.get("eodhd_symbol"):
            return str(old["eodhd_symbol"]), "CACHE"
        if str(old.get("status") or "").upper() == "UNRESOLVED":
            return None, "UNRESOLVED_CACHED"

    body = _get_json(f"/search/{isin}", token, {"limit": 20})
    rows = body if isinstance(body, list) else (body.get("data", []) if isinstance(body, dict) else [])
    exact = [r for r in rows if str((r or {}).get("ISIN") or "").strip().upper() == isin.upper()]
    if not exact:
        now = datetime.now(timezone.utc).isoformat()
        with cache_lock:
            cache[isin] = {"isin": isin, "yahoo_ticker": yahoo_ticker, "eodhd_symbol": "", "status": "UNRESOLVED", "updated_at": now}
        return None, "UNRESOLVED"

    exact.sort(key=lambda r: (bool((r or {}).get("isPrimary")), str((r or {}).get("Exchange") or "")), reverse=True)
    row = exact[0]
    code = str(row.get("Code") or "").strip()
    exchange = str(row.get("Exchange") or "").strip()
    symbol = code if "." in code or not exchange else f"{code}.{exchange}"
    if not symbol:
        return None, "UNRESOLVED"
    now = datetime.now(timezone.utc).isoformat()
    with cache_lock:
        cache[isin] = {"isin": isin, "yahoo_ticker": yahoo_ticker, "eodhd_symbol": symbol, "status": "RESOLVED", "updated_at": now}
    return symbol, "SEARCH"


def _latest_reports(section) -> list[dict]:
    if not isinstance(section, dict):
        return []
    rows = []
    for key, value in section.items():
        if isinstance(value, dict):
            row = dict(value)
            row.setdefault("_key", key)
            rows.append(row)
    def stamp(r):
        return str(r.get("date") or r.get("filing_date") or r.get("_key") or "")
    return sorted(rows, key=stamp, reverse=True)


def _first_num(row: dict, names: list[str]):
    for name in names:
        v = _f(row.get(name))
        if v is not None:
            return v
    return None


def _ttm(section, names: list[str]) -> float | None:
    rows = _latest_reports(section)[:4]
    if not rows:
        return None
    vals = []
    for row in rows:
        v = _first_num(row, names)
        if v is None:
            return None
        vals.append(v)
    return sum(vals)


def _extract_fundamentals(body: dict) -> dict:
    general = body.get("General", {}) if isinstance(body, dict) else {}
    highlights = body.get("Highlights", {}) if isinstance(body, dict) else {}
    valuation = body.get("Valuation", {}) if isinstance(body, dict) else {}
    technicals = body.get("Technicals", {}) if isinstance(body, dict) else {}
    shares = body.get("SharesStats", {}) if isinstance(body, dict) else {}
    splits = body.get("SplitsDividends", {}) if isinstance(body, dict) else {}
    financials = body.get("Financials", {}) if isinstance(body, dict) else {}

    balance = financials.get("Balance_Sheet", {}) if isinstance(financials, dict) else {}
    income = financials.get("Income_Statement", {}) if isinstance(financials, dict) else {}
    cashflow = financials.get("Cash_Flow", {}) if isinstance(financials, dict) else {}
    bq = _latest_reports(balance.get("quarterly", {}))
    iq = income.get("quarterly", {}) if isinstance(income, dict) else {}
    cq = cashflow.get("quarterly", {}) if isinstance(cashflow, dict) else {}
    latest_b = bq[0] if bq else {}

    total_debt = _first_num(latest_b, ["shortLongTermDebtTotal", "longTermDebtTotal", "longTermDebt", "shortTermDebt"])
    cash = _first_num(latest_b, ["cashAndEquivalents", "cash", "cashAndShortTermInvestments"])
    equity = _first_num(latest_b, ["totalStockholderEquity", "totalEquity", "stockholdersEquity"])
    current_assets = _first_num(latest_b, ["totalCurrentAssets", "currentAssets"])
    current_liab = _first_num(latest_b, ["totalCurrentLiabilities", "currentLiabilities"])
    ebitda = _ttm(iq, ["ebitda"])
    operating_income = _ttm(iq, ["operatingIncome"])
    interest_expense = _ttm(iq, ["interestExpense", "interestExpenseNonOperating"])
    fcf = _ttm(cq, ["freeCashFlow"])
    if fcf is None:
        opcf = _ttm(cq, ["totalCashFromOperatingActivities", "operatingCashFlow"])
        capex = _ttm(cq, ["capitalExpenditures", "capitalExpenditure"])
        if opcf is not None and capex is not None:
            fcf = opcf + capex if capex < 0 else opcf - capex
    opcf = _ttm(cq, ["totalCashFromOperatingActivities", "operatingCashFlow"])

    market_cap = _f(highlights.get("MarketCapitalization"))
    result = {
        "eodhd_returned_isin": str(general.get("ISIN") or "").strip(),
        "sector_v21": general.get("Sector"),
        "industry_v21": general.get("Industry"),
        "market_cap_v21": market_cap,
        "enterprise_value_v21": _f(valuation.get("EnterpriseValue")),
        "per_ttm_v21": _f(highlights.get("PERatio")) or _f(valuation.get("TrailingPE")),
        "per_forward_v21": _f(valuation.get("ForwardPE")),
        "pb_v21": _f(valuation.get("PriceBookMRQ")),
        "roe_v21_pct": _pct(highlights.get("ReturnOnEquityTTM")),
        "roa_v21_pct": _pct(highlights.get("ReturnOnAssetsTTM")),
        "operating_margin_v21_pct": _pct(highlights.get("OperatingMarginTTM")),
        "net_margin_v21_pct": _pct(highlights.get("ProfitMargin")),
        "revenue_growth_v21_pct": _pct(highlights.get("QuarterlyRevenueGrowthYOY")),
        "earnings_growth_v21_pct": _pct(highlights.get("QuarterlyEarningsGrowthYOY")),
        "dividend_yield_v21_pct": _pct(highlights.get("DividendYield")),
        "payout_ratio_v21_pct": _pct(splits.get("PayoutRatio")) or _pct(highlights.get("PayoutRatio")),
        "target_mean_v21": _f(highlights.get("WallStreetTargetPrice")),
        "beta_v21": _f(technicals.get("Beta")),
        "high_52w": _f(technicals.get("52WeekHigh")),
        "low_52w": _f(technicals.get("52WeekLow")),
        "institutional_ownership_pct": _pct(shares.get("PercentInstitutions")),
        "insider_ownership_pct": _pct(shares.get("PercentInsiders")),
        "short_percent_float_pct": _pct(shares.get("ShortPercentFloat")),
        "forward_eps_current_year_v21": _f(highlights.get("EarningsShare")),
        "forward_eps_next_year_v21": _f(highlights.get("EPSEstimateNextYear")),
        "forward_eps_current_quarter_v21": _f(highlights.get("EPSEstimateCurrentQuarter")),
        "forward_eps_next_quarter_v21": _f(highlights.get("EPSEstimateNextQuarter")),
        "total_debt_v21": total_debt,
        "total_cash_v21": cash,
        "ebitda_v21": ebitda,
        "free_cash_flow_v21": fcf,
        "operating_cash_flow_v21": opcf,
        "debt_to_ebitda_v21": (total_debt / ebitda) if total_debt is not None and ebitda not in (None, 0) else None,
        "debt_to_equity_v21": (total_debt / equity) if total_debt is not None and equity not in (None, 0) else None,
        "current_ratio_v21": (current_assets / current_liab) if current_assets is not None and current_liab not in (None, 0) else None,
        "interest_coverage_v21": (operating_income / abs(interest_expense)) if operating_income is not None and interest_expense not in (None, 0) else None,
        "fcf_yield_v21": (fcf / market_cap * 100.0) if fcf is not None and market_cap not in (None, 0) else None,
    }
    return result


def _parse_trend_row(row: dict) -> dict:
    period = str(row.get("period") or row.get("Period") or "").strip().lower()
    return {
        "period": period,
        "earnings_avg": _f(row.get("earningsEstimateAvg")),
        "earnings_low": _f(row.get("earningsEstimateLow")),
        "earnings_high": _f(row.get("earningsEstimateHigh")),
        "earnings_analysts": _f(row.get("earningsEstimateNumberOfAnalysts")),
        "revenue_avg": _f(row.get("revenueEstimateAvg")),
        "revenue_low": _f(row.get("revenueEstimateLow")),
        "revenue_high": _f(row.get("revenueEstimateHigh")),
        "revenue_analysts": _f(row.get("revenueEstimateNumberOfAnalysts")),
        "earnings_growth": _pct(row.get("earningsEstimateGrowth")),
        "revenue_growth": _pct(row.get("revenueEstimateGrowth")),
        "eps_current": _f(row.get("epsTrendCurrent")),
        "eps_7d": _f(row.get("epsTrend7daysAgo")),
        "eps_30d": _f(row.get("epsTrend30daysAgo")),
        "eps_60d": _f(row.get("epsTrend60daysAgo")),
        "eps_90d": _f(row.get("epsTrend90daysAgo")),
        "up_30d": _f(row.get("epsRevisionsUpLast30days")),
        "down_30d": _f(row.get("epsRevisionsDownLast30days")),
    }


def main() -> None:
    token = str(os.getenv("EODHD_API_KEY") or "").strip()
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != 1429 or df["isin"].astype(str).nunique() != 1429:
        raise RuntimeError("EODHD backfill requires canonical 1429 Actions universe")

    tracked = [
        "market_cap_v21", "per_forward_v21", "pb_v21", "fcf_yield_v21", "roe_v21_pct",
        "roa_v21_pct", "operating_margin_v21_pct", "net_margin_v21_pct", "revenue_growth_v21_pct",
        "earnings_growth_v21_pct", "debt_to_ebitda_v21", "debt_to_equity_v21", "dividend_yield_v21_pct",
        "target_mean_v21",
    ]
    before = {f: round(float(df[f].notna().mean() * 100), 2) if f in df else 0.0 for f in tracked}
    audit = {
        "passed": True, "status": "ACTIVE" if token else "SKIPPED_NO_KEY", "rows": len(df),
        "max_symbols": MAX_SYMBOLS, "workers": WORKERS, "attempted": 0, "resolved": 0,
        "fundamentals_observed": 0, "identity_rejected": 0, "unresolved": 0, "filled_cells": 0,
        "trend_batches": 0, "trend_symbols": 0, "trend_rows": 0, "errors": [],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if not token:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        print("V21_ACTIONS_EODHD_BACKFILL_SKIPPED_NO_KEY")
        return

    score = pd.to_numeric(df.get("v210_enrichment_priority_score"), errors="coerce").fillna(-1)
    needs = pd.Series(False, index=df.index)
    for field in tracked:
        needs |= df[field].isna() if field in df else True
    candidates = list(df.index[needs])
    candidates.sort(key=lambda i: float(score.loc[i]), reverse=True)
    candidates = candidates[:MAX_SYMBOLS]

    cache = _load_cache()
    cache_lock = Lock()
    collected: dict[int, tuple[str, dict]] = {}

    def collect(i: int):
        isin = str(df.at[i, "isin"] or "").strip().upper()
        ticker = str(df.at[i, "yahoo_ticker"] or "").strip()
        symbol, status = _resolve(isin, ticker, token, cache, cache_lock)
        if not symbol:
            return i, None, status, None
        body = _get_json(f"/fundamentals/{symbol}", token)
        metrics = _extract_fundamentals(body if isinstance(body, dict) else {})
        returned_isin = str(metrics.pop("eodhd_returned_isin", "") or "").strip().upper()
        if returned_isin and returned_isin != isin:
            return i, symbol, "IDENTITY_MISMATCH", {"returned_isin": returned_isin}
        return i, symbol, "OK", metrics

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(collect, i): i for i in candidates}
        for future in as_completed(futures):
            audit["attempted"] += 1
            i = futures[future]
            try:
                _, symbol, status, payload = future.result()
                if status.startswith("UNRESOLVED"):
                    audit["unresolved"] += 1
                    continue
                if status == "IDENTITY_MISMATCH":
                    audit["identity_rejected"] += 1
                    if len(audit["errors"]) < 80:
                        audit["errors"].append({"isin": str(df.at[i, "isin"]), "symbol": symbol, "stage": "identity", **(payload or {})})
                    continue
                audit["resolved"] += 1
                audit["fundamentals_observed"] += 1
                collected[i] = (str(symbol), payload or {})
            except Exception as exc:
                if len(audit["errors"]) < 80:
                    audit["errors"].append({"isin": str(df.at[i, "isin"]), "stage": "fundamentals", "error": f"{type(exc).__name__}: {str(exc)[:180]}"})

    checked = datetime.now(timezone.utc).isoformat()
    symbol_to_idx: dict[str, int] = {}
    for i, (symbol, metrics) in collected.items():
        symbol_to_idx[symbol] = i
        audit["filled_cells"] += _fill(df, i, "eodhd_symbol_v21", symbol)
        audit["filled_cells"] += _fill(df, i, "eodhd_checked_at_utc", checked)
        for field, value in metrics.items():
            audit["filled_cells"] += _fill(df, i, field, value)

    symbols = list(symbol_to_idx)
    trend_period_map = {
        "0q": "current_q", "+1q": "next_q", "1q": "next_q",
        "0y": "current_y", "+1y": "next_y", "1y": "next_y",
    }
    for start in range(0, len(symbols), TREND_BATCH_SIZE):
        batch = symbols[start:start + TREND_BATCH_SIZE]
        if not batch:
            continue
        try:
            body = _get_json("/calendar/trends", token, {"symbols": ",".join(batch)})
            audit["trend_batches"] += 1
            audit["trend_symbols"] += len(batch)
            rows = []
            if isinstance(body, list):
                rows = body
            elif isinstance(body, dict):
                for key in ["data", "trends", "results"]:
                    if isinstance(body.get(key), list):
                        rows = body[key]
                        break
            for raw in rows:
                symbol = str(raw.get("code") or raw.get("symbol") or raw.get("Code") or raw.get("Symbol") or "").strip()
                i = symbol_to_idx.get(symbol)
                if i is None and "." not in symbol:
                    matches = [idx for s, idx in symbol_to_idx.items() if s.split(".", 1)[0] == symbol]
                    i = matches[0] if len(matches) == 1 else None
                if i is None:
                    continue
                trend = _parse_trend_row(raw)
                suffix = trend_period_map.get(trend["period"])
                if not suffix:
                    continue
                audit["trend_rows"] += 1
                mapping = {
                    f"eps_estimate_{suffix}_v21": trend["earnings_avg"],
                    f"eps_estimate_low_{suffix}_v21": trend["earnings_low"],
                    f"eps_estimate_high_{suffix}_v21": trend["earnings_high"],
                    f"eps_estimate_analysts_{suffix}_v21": trend["earnings_analysts"],
                    f"revenue_estimate_{suffix}_v21": trend["revenue_avg"],
                    f"revenue_estimate_low_{suffix}_v21": trend["revenue_low"],
                    f"revenue_estimate_high_{suffix}_v21": trend["revenue_high"],
                    f"revenue_estimate_analysts_{suffix}_v21": trend["revenue_analysts"],
                    f"eps_estimate_growth_{suffix}_pct_v21": trend["earnings_growth"],
                    f"revenue_estimate_growth_{suffix}_pct_v21": trend["revenue_growth"],
                    f"eps_trend_current_{suffix}_v21": trend["eps_current"],
                    f"eps_trend_7d_{suffix}_v21": trend["eps_7d"],
                    f"eps_trend_30d_{suffix}_v21": trend["eps_30d"],
                    f"eps_trend_60d_{suffix}_v21": trend["eps_60d"],
                    f"eps_trend_90d_{suffix}_v21": trend["eps_90d"],
                    f"eps_revisions_up_30d_{suffix}_v21": trend["up_30d"],
                    f"eps_revisions_down_30d_{suffix}_v21": trend["down_30d"],
                }
                for field, value in mapping.items():
                    audit["filled_cells"] += _fill(df, i, field, value)
                if trend["up_30d"] is not None or trend["down_30d"] is not None:
                    net = (trend["up_30d"] or 0.0) - (trend["down_30d"] or 0.0)
                    audit["filled_cells"] += _fill(df, i, f"net_eps_revisions_30d_{suffix}_v21", net)
                if trend["eps_current"] is not None and trend["eps_30d"] not in (None, 0):
                    change = (trend["eps_current"] - trend["eps_30d"]) / abs(trend["eps_30d"]) * 100.0
                    audit["filled_cells"] += _fill(df, i, f"eps_estimate_change_30d_pct_{suffix}_v21", change)
                    if suffix in {"current_y", "next_y"}:
                        audit["filled_cells"] += _fill(df, i, "estimate_revision_score_v21", max(0.0, min(100.0, 50.0 + change * 5.0)))
        except Exception as exc:
            audit["errors"].append({"stage": "earnings_trends", "batch_start": start, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})

    last = pd.to_numeric(df.get("last_close"), errors="coerce")
    target = pd.to_numeric(df.get("target_mean_v21"), errors="coerce")
    df["target_upside_pct_v21"] = ((target / last) - 1.0) * 100.0
    df.loc[last.le(0) | last.isna() | target.isna(), "target_upside_pct_v21"] = np.nan
    df["potential_gt_15_flag"] = df["target_upside_pct_v21"].ge(15).where(df["target_upside_pct_v21"].notna())

    _save_cache(cache)
    df.to_csv(TARGET, sep=";", index=False, encoding="utf-8-sig")
    after = {f: round(float(df[f].notna().mean() * 100), 2) if f in df else 0.0 for f in tracked}
    audit["coverage_before_pct"] = before
    audit["coverage_after_pct"] = after
    audit["coverage_gain_points"] = {f: round(after[f] - before[f], 2) for f in tracked}
    audit["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print("V21_ACTIONS_EODHD_BACKFILL_OK", json.dumps({
        "attempted": audit["attempted"], "resolved": audit["resolved"],
        "fundamentals_observed": audit["fundamentals_observed"], "trend_rows": audit["trend_rows"],
        "filled_cells": audit["filled_cells"], "coverage_after": after,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
