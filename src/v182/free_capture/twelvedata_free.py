from __future__ import annotations

import os
import time
from datetime import date
import requests
import pandas as pd

from .core import CaptureStore, clean_text, number, utcnow

BASE = "https://api.twelvedata.com"
SUFFIX_MIC = {
    ".PA": "XPAR", ".AS": "XAMS", ".BR": "XBRU", ".LS": "XLIS", ".MI": "XMIL",
    ".IR": "XDUB", ".OL": "XOSL", ".DE": "XETR", ".MC": "XMAD", ".ST": "XSTO",
    ".CO": "XCSE", ".HE": "XHEL", ".VI": "XWBO"
}


def _symbol_mic(row: pd.Series) -> tuple[str, str]:
    direct = clean_text(row.get("euronext_symbol"))
    mic = clean_text(row.get("euronext_mic")).upper()
    yt = clean_text(row.get("yahoo_ticker"))
    if direct and mic:
        return direct, mic
    for suffix, suffix_mic in SUFFIX_MIC.items():
        if yt.upper().endswith(suffix):
            return yt[:-len(suffix)], mic or suffix_mic
    return yt.split(".")[0], mic


def _api_usage(session: requests.Session, key: str) -> dict:
    try:
        r = session.get(f"{BASE}/api_usage", params={"apikey": key}, timeout=20)
        return r.json() if r.ok else {"status_code": r.status_code}
    except Exception as exc:
        return {"error": str(exc)}


def capture(universe: pd.DataFrame, store: CaptureStore, max_symbols: int = 700, credit_guard: int = 760) -> dict:
    key = os.getenv("TWELVEDATA_API_KEY", "").strip()
    if not key:
        store.add_health("TWELVEDATA_FREE", "SKIPPED_NO_KEY", message="TWELVEDATA_API_KEY absent")
        return {"status": "SKIPPED_NO_KEY"}

    session = requests.Session()
    usage_before = _api_usage(session, key)
    market = store.market()
    latest: dict[str, str] = {}
    if not market.empty:
        m = market.loc[market["source"].eq("TWELVEDATA_FREE")]
        if not m.empty:
            latest = m.groupby("isin")["date"].max().astype(str).to_dict()

    today = date.today().isoformat()
    targets = []
    for _, row in universe.iterrows():
        isin = str(row["isin"])
        if latest.get(isin) == today:
            continue
        symbol, mic = _symbol_mic(row)
        if symbol and mic:
            targets.append((row, symbol, mic))
        if len(targets) >= max_symbols:
            break

    rows: list[dict] = []
    failed = 0
    blocked_mics: set[str] = set()
    calls = 0
    for row, symbol, mic in targets:
        if mic in blocked_mics or calls >= credit_guard:
            continue
        params = {
            "symbol": symbol, "mic_code": mic, "interval": "1day", "outputsize": 7,
            "order": "ASC", "timezone": "UTC", "apikey": key
        }
        try:
            r = session.get(f"{BASE}/time_series", params=params, timeout=25)
            calls += 1
            data = r.json()
            if not r.ok or data.get("status") == "error" or not data.get("values"):
                failed += 1
                msg = clean_text(data.get("message") or data.get("code") or r.status_code)
                if any(x in msg.lower() for x in ["plan", "not available", "access", "subscription"]):
                    blocked_mics.add(mic)
                continue
            meta = data.get("meta") or {}
            for v in data.get("values") or []:
                rows.append({
                    "isin": str(row["isin"]), "date": clean_text(v.get("datetime"))[:10],
                    "open": number(v.get("open")), "high": number(v.get("high")),
                    "low": number(v.get("low")), "close": number(v.get("close")),
                    "volume": number(v.get("volume")), "currency": clean_text(meta.get("currency")),
                    "source": "TWELVEDATA_FREE", "ticker": clean_text(meta.get("symbol") or symbol),
                    "mic": clean_text(meta.get("mic_code") or mic), "observed_at_utc": utcnow(),
                })
        except Exception:
            failed += 1
        time.sleep(8.8)  # below the public Basic 8 credits/minute ceiling

    added = store.upsert_market(rows)
    usage_after = _api_usage(session, key)
    left = usage_after.get("api_credits_left", usage_after.get("credits_left", ""))
    store.add_health("TWELVEDATA_FREE", "OK" if added else "NO_NEW_DATA", len(targets), added, failed,
                     calls, left, f"blocked_mics={','.join(sorted(blocked_mics))}; usage={usage_after}")
    return {
        "status": "OK", "attempted": min(len(targets), calls), "calls": calls, "added_rows": added,
        "failed": failed, "blocked_mics": sorted(blocked_mics), "usage_before": usage_before,
        "usage_after": usage_after
    }
