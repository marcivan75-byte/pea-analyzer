from __future__ import annotations

import math
import os
from datetime import date
import time
import pandas as pd

from v182.sources import alpha_vantage as av
from .core import CaptureStore, clean_text, is_observed, number, utcnow

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


def _rank(universe: pd.DataFrame) -> pd.DataFrame:
    """Spend scarce Alpha calls on securities most likely to exist in its global catalogue."""
    x = universe.copy()
    idx = x.index
    target_fields = sorted({field for field, _ in FIELD_MAP.values()})
    missing = pd.Series(0.0, index=idx)
    for field in target_fields:
        if field in x:
            missing += (~x[field].map(is_observed)).astype(float)
        else:
            missing += 1.0

    mc = pd.to_numeric(x.get("market_cap_v21", pd.Series(0, index=idx)), errors="coerce").fillna(0).clip(lower=0)
    analysts = pd.to_numeric(x.get("n_analysts_v21", pd.Series(0, index=idx)), errors="coerce").fillna(0).clip(lower=0)
    mt = pd.to_numeric(x.get("score_mt", pd.Series(0, index=idx)), errors="coerce").fillna(0)
    lt = pd.to_numeric(x.get("score_lt", pd.Series(0, index=idx)), errors="coerce").fillna(0)
    selected = x.get("selection_mt", pd.Series(False, index=idx)).astype(str).str.lower().isin({"true", "1", "yes"})
    selected |= x.get("selection_lt", pd.Series(False, index=idx)).astype(str).str.lower().isin({"true", "1", "yes"})

    has_analysts = analysts.gt(0)
    meaningful_cap = mc.ge(200_000_000)
    eligible = missing.gt(0) & (has_analysts | meaningful_cap)

    # Capture ranking only: this cannot modify investment scores or horizon weights.
    log_cap = mc.map(lambda v: max(0.0, math.log10(v) - 7.0) if v > 0 else 0.0)
    x["_alpha_rank"] = (
        has_analysts.astype(float) * 50_000.0
        + analysts.clip(upper=30) * 1_500.0
        + log_cap.clip(upper=5) * 3_000.0
        + selected.astype(float) * 5_000.0
        + missing * 250.0
        + (mt + lt) / 4.0
    )
    x["_alpha_missing"] = missing
    return x[eligible].sort_values("_alpha_rank", ascending=False, kind="stable")


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

    ranked = _rank(universe)
    rows: list[dict] = []
    calls = 0
    attempted = 0
    cached_negative_skipped = 0
    failed = 0
    resolved_count = 0
    samples: list[dict] = []
    today = date.today().isoformat()

    for _, row in ranked.iterrows():
        if attempted >= max_symbols or calls >= max_calls - 1:
            break
        isin = str(row["isin"])
        if all((isin, f) in existing for f, _ in FIELD_MAP.values()):
            continue
        try:
            resolved = av.resolve_symbol(row.to_dict(), key, cache_path=cache_path)
            calls += int(resolved.api_calls)
            if not resolved.symbol:
                if resolved.source == "CACHE" and int(resolved.api_calls) == 0:
                    cached_negative_skipped += 1
                    continue
                attempted += 1
                failed += 1
                if len(samples) < 6:
                    samples.append({"isin": isin, "name": clean_text(row.get("name")), "status": resolved.reason or "UNRESOLVED"})
                continue

            attempted += 1
            resolved_count += 1
            if calls >= max_calls:
                failed += 1
                break
            body = av._request(key, "OVERVIEW", symbol=resolved.symbol)
            calls += 1
            if not body or clean_text(body.get("Symbol")) == "":
                failed += 1
                continue
            fields = []
            for api_field, (field, mult) in FIELD_MAP.items():
                val = number(body.get(api_field))
                if val is None:
                    continue
                fields.append(field)
                rows.append({
                    "isin": isin, "field": field, "value": val * mult, "value_text": "",
                    "as_of": today, "source": "ALPHA_VANTAGE_FREE", "evidence": "API_OVERVIEW_RESOLVED_SYMBOL",
                    "confidence": 0.80, "status": "OBSERVED", "observed_at_utc": utcnow(),
                })
            if len(samples) < 6:
                samples.append({"isin": isin, "name": clean_text(row.get("name")), "symbol": resolved.symbol,
                                "region": resolved.region, "match_score": resolved.match_score, "fields": fields})
        except Exception as exc:
            attempted += 1
            failed += 1
            if len(samples) < 6:
                samples.append({"isin": isin, "name": clean_text(row.get("name")), "error": f"{type(exc).__name__}:{str(exc)[:120]}"})
        time.sleep(1.15)

    added = store.upsert_facts(rows)
    status = "OK" if added else "NO_NEW_DATA"
    store.add_health(
        "ALPHA_VANTAGE_FREE", status, attempted, added, failed,
        calls, max(0, max_calls - calls),
        f"OVERVIEW + cached SYMBOL_SEARCH; cached_negative_skipped={cached_negative_skipped}; resolved={resolved_count}; samples={samples}; hard daily guard",
    )
    return {
        "status": status,
        "attempted": attempted,
        "calls": calls,
        "facts_added": added,
        "failed": failed,
        "resolved": resolved_count,
        "cached_negative_skipped": cached_negative_skipped,
        "samples": samples,
    }
