from __future__ import annotations

import os
from datetime import date
from pathlib import Path
import time
import pandas as pd

from v182.sources import alpha_vantage as av
from .core import CaptureStore, clean_text, number, utcnow

FIELD_MAP = {
    "MarketCapitalization": ("market_cap_v21", 1.0),
    "ForwardPE": ("per_forward_v21", 1.0),
    "PriceToBookRatio": ("pb_v21", 1.0),
    "ReturnOnEquityTTM": ("roe_v21_pct", 100.0),
    "ReturnOnAssetsTTM": ("roa_v21_pct", 100.0),
    "OperatingMarginTTM": ("operating_margin_v21_pct", 100.0),
    "ProfitMargin": ("net_margin_v21_pct", 100.0),
    "QuarterlyRevenueGrowthYOY": ("revenue_growth_v21_pct", 100.0),
    "QuarterlyEarningsGrowthYOY": ("earnings_growth_v21_pct", 100.0),
    "DividendYield": ("dividend_yield_v21_pct", 100.0),
    "AnalystTargetPrice": ("target_mean_v21", 1.0),
    "EPS": ("eps_ttm_v21", 1.0),
    "RevenuePerShareTTM": ("revenue_per_share_v21", 1.0),
    "Beta": ("beta_v21", 1.0),
}


def capture(universe: pd.DataFrame, store: CaptureStore, max_symbols: int = 10, max_calls: int = 23) -> dict:
    key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    if not key:
        store.add_health("ALPHA_VANTAGE_FREE", "SKIPPED_NO_KEY", message="ALPHA_VANTAGE_API_KEY absent")
        return {"status": "SKIPPED_NO_KEY"}

    cache_path = store.root / "cache" / "alpha_symbol_map.csv"
    facts = store.facts()
    existing: set[tuple[str, str]] = set()
    if not facts.empty:
        recent = facts[facts["source"].eq("ALPHA_VANTAGE_FREE")]
        existing = set(zip(recent["isin"].astype(str), recent["field"].astype(str)))

    rows: list[dict] = []
    calls = 0
    attempted = 0
    failed = 0
    today = date.today().isoformat()
    for _, row in universe.iterrows():
        if attempted >= max_symbols or calls >= max_calls - 1:
            break
        isin = str(row["isin"])
        if all((isin, f) in existing for f, _ in FIELD_MAP.values()):
            continue
        attempted += 1
        try:
            resolved = av.resolve_symbol(row.to_dict(), key, cache_path=cache_path)
            calls += int(resolved.api_calls)
            if not resolved.symbol or calls >= max_calls:
                failed += 1
                continue
            body = av._request(key, "OVERVIEW", symbol=resolved.symbol)
            calls += 1
            if not body or clean_text(body.get("Symbol")) == "":
                failed += 1
                continue
            for api_field, (field, mult) in FIELD_MAP.items():
                val = number(body.get(api_field))
                if val is None:
                    continue
                rows.append({
                    "isin": isin, "field": field, "value": val * mult, "value_text": "",
                    "as_of": today, "source": "ALPHA_VANTAGE_FREE", "evidence": "B",
                    "confidence": 0.80, "status": "OBSERVED", "observed_at_utc": utcnow(),
                })
        except Exception:
            failed += 1
        time.sleep(1.15)

    added = store.upsert_facts(rows)
    store.add_health("ALPHA_VANTAGE_FREE", "OK" if added else "NO_NEW_DATA", attempted, added, failed,
                     calls, max(0, max_calls - calls), "OVERVIEW + cached SYMBOL_SEARCH; hard daily guard")
    return {"status": "OK", "attempted": attempted, "calls": calls, "facts_added": added, "failed": failed}
