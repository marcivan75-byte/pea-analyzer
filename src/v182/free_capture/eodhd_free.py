from __future__ import annotations

from datetime import date, timedelta
import os
import time

import pandas as pd
import requests

from .core import CaptureStore, is_observed, number, utcnow


SOURCE = "EODHD_FREE_EOD"
BASE = "https://eodhd.com/api/eod"


def _ticker(row: pd.Series) -> str:
    for field in ("eodhd_ticker", "yahoo_ticker", "ticker_yahoo_final"):
        value = str(row.get(field) or "").strip()
        if value and value.lower() != "nan":
            return value
    return ""


def _needs_history(row: pd.Series, store_market_isin: set[str]) -> bool:
    isin = str(row.get("isin") or "")
    if isin in store_market_isin:
        return False
    watched = ("last_close", "volume", "high_52w", "low_52w")
    return any(not is_observed(row.get(field)) for field in watched)


def _price_match(row: pd.Series, observed: float, tolerance: float = 0.30) -> bool:
    canonical = number(row.get("last_close"))
    if canonical is None or canonical <= 0:
        return True
    ratio = observed / canonical if canonical else None
    return ratio is not None and (1.0 - tolerance) <= ratio <= (1.0 + tolerance)


def capture(prioritized: pd.DataFrame, store: CaptureStore, max_symbols: int = 18) -> dict:
    token = str(os.getenv("EODHD_API_KEY") or "").strip()
    if not token:
        store.add_health(SOURCE, "SKIPPED_NO_KEY", message="EODHD_API_KEY missing")
        return {"status": "SKIPPED_NO_KEY", "attempted": 0, "market_rows_added": 0}

    # Official free plan is 20 calls/day. Keep a safety reserve for diagnostics/manual tests.
    hard_guard = min(18, max(0, int(max_symbols)))
    existing = store.market()
    existing_isin = set(existing["isin"].astype(str)) if not existing.empty else set()
    candidates = prioritized.loc[prioritized.apply(lambda row: _needs_history(row, existing_isin), axis=1)].head(hard_guard)
    if candidates.empty:
        store.add_health(SOURCE, "NO_MISSING_MARKET_HISTORY")
        return {"status": "NO_MISSING_MARKET_HISTORY", "attempted": 0, "market_rows_added": 0}

    session = requests.Session()
    rows: list[dict] = []
    attempted = matched = failed = rejected_identity = 0
    start = (date.today() - timedelta(days=370)).isoformat()
    end = date.today().isoformat()

    for _, row in candidates.iterrows():
        ticker = _ticker(row)
        if not ticker:
            failed += 1
            continue
        attempted += 1
        try:
            response = session.get(
                f"{BASE}/{ticker}",
                params={
                    "api_token": token,
                    "fmt": "json",
                    "period": "d",
                    "from": start,
                    "to": end,
                    "order": "a",
                },
                headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-V21.1-FreeCapture/1.3")},
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or not payload:
                failed += 1
                continue
            clean = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                close = number(item.get("adjusted_close")) or number(item.get("close"))
                if close is None or close <= 0:
                    continue
                clean.append({
                    "date": str(item.get("date") or ""),
                    "open": number(item.get("open")),
                    "high": number(item.get("high")),
                    "low": number(item.get("low")),
                    "close": close,
                    "volume": number(item.get("volume")),
                })
            if not clean:
                failed += 1
                continue
            latest = clean[-1]["close"]
            if not _price_match(row, float(latest)):
                rejected_identity += 1
                continue
            isin = str(row.get("isin") or "")
            currency = str(row.get("currency") or row.get("trading_currency") or "")
            mic = str(row.get("euronext_mic") or row.get("mic") or "")
            for item in clean:
                rows.append({
                    "isin": isin,
                    "date": item["date"],
                    "open": item["open"] if item["open"] is not None else "",
                    "high": item["high"] if item["high"] is not None else "",
                    "low": item["low"] if item["low"] is not None else "",
                    "close": item["close"],
                    "volume": item["volume"] if item["volume"] is not None else "",
                    "currency": currency,
                    "source": SOURCE,
                    "ticker": ticker,
                    "mic": mic,
                    "observed_at_utc": utcnow(),
                })
            matched += 1
        except Exception:
            failed += 1
        time.sleep(0.25)

    added = store.upsert_market(rows)
    status = "OK" if matched else ("IDENTITY_REJECTED" if rejected_identity else "NO_NEW_DATA")
    store.add_health(
        SOURCE,
        status,
        attempted=attempted,
        succeeded=matched,
        failed=failed + rejected_identity,
        quota_used=attempted,
        quota_left=max(0, 20 - attempted),
        message=f"Free-plan hard guard 18/20 calls; rows={added}; identity_rejected={rejected_identity}; one-year EOD only",
    )
    return {
        "status": status,
        "attempted": attempted,
        "matched": matched,
        "identity_rejected": rejected_identity,
        "failed": failed,
        "market_rows_added": added,
        "free_plan_guard_calls": 18,
        "free_plan_documented_calls_per_day": 20,
        "history_window": "PAST_YEAR",
    }
