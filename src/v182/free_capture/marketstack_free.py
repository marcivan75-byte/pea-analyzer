from __future__ import annotations

import os
import pandas as pd

from v182.sources.marketstack_eod import fetch_eod_history
from .core import CaptureStore, clean_text, utcnow
from .twelvedata_free import _symbol_mic


def capture(universe: pd.DataFrame, store: CaptureStore, max_symbols: int = 3) -> dict:
    key = os.getenv("MARKETSTACK_API_KEY", "").strip()
    if not key:
        store.add_health("MARKETSTACK_FREE", "SKIPPED_NO_KEY", message="MARKETSTACK_API_KEY absent")
        return {"status": "SKIPPED_NO_KEY"}
    specs = []
    by_canonical: dict[str, tuple[str, str, str]] = {}
    market = store.market()
    covered = set(market["isin"].astype(str)) if not market.empty else set()
    for _, row in universe.iterrows():
        isin = str(row["isin"])
        if isin in covered:
            continue
        symbol, mic = _symbol_mic(row)
        canonical = clean_text(row.get("yahoo_ticker")) or symbol
        if not symbol or not mic or not canonical:
            continue
        specs.append({"canonical_ticker": canonical, "symbol": symbol, "expected_mic": mic})
        by_canonical[canonical] = (isin, symbol, mic)
        if len(specs) >= max_symbols:
            break
    result = fetch_eod_history(specs, key, history_days=366, max_symbols=max_symbols, min_rows=20)
    rows = []
    for canonical, frame in result.frames.items():
        isin, symbol, mic = by_canonical[canonical]
        for idx, r in frame.tail(370).iterrows():
            rows.append({
                "isin": isin, "date": pd.Timestamp(idx).date().isoformat(), "open": r.get("Open"),
                "high": r.get("High"), "low": r.get("Low"), "close": r.get("Close"),
                "volume": r.get("Volume"), "currency": "", "source": "MARKETSTACK_FREE",
                "ticker": symbol, "mic": mic, "observed_at_utc": utcnow(),
            })
    added = store.upsert_market(rows)
    store.add_health("MARKETSTACK_FREE", "OK" if added else "NO_NEW_DATA", result.attempted, added,
                     len(result.failures), result.attempted, "", str(result.failures[:3]))
    return {"status": "OK", "attempted": result.attempted, "successful": result.successful,
            "market_rows_added": added, "failures": result.failures[:10]}
