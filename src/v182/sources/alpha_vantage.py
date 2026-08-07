from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import time

BASE_URL = "https://www.alphavantage.co/query"
CACHE_FIELDS = ["isin", "yahoo_ticker", "name", "country", "alpha_symbol", "region", "match_score", "status", "updated_at"]

COUNTRY_ALIASES = {
    "FR": {"france"}, "NL": {"netherlands"}, "BE": {"belgium"}, "DE": {"germany"},
    "IT": {"italy"}, "ES": {"spain"}, "PT": {"portugal"}, "AT": {"austria"},
    "IE": {"ireland"}, "FI": {"finland"}, "SE": {"sweden"}, "DK": {"denmark"},
    "NO": {"norway"}, "LU": {"luxembourg"}, "CH": {"switzerland"}, "GB": {"united kingdom", "uk"},
}

OVERVIEW_FIELDS = {
    "MarketCapitalization": "market_cap",
    "PERatio": "per_ttm",
    "ForwardPE": "per_forward",
    "PriceToBookRatio": "pb",
    "ReturnOnEquityTTM": "roe_api",
    "OperatingMarginTTM": "marge_ebit",
    "ProfitMargin": "marge_nette",
    "DividendYield": "dividend_yield_pct",
    "Beta": "beta",
}


@dataclass(frozen=True)
class AlphaResolutionResult:
    symbol: str | None
    source: str
    region: str = ""
    match_score: float = 0.0
    api_calls: int = 0
    reason: str = ""


def _api_error(body) -> str | None:
    if not isinstance(body, dict):
        return "INVALID_JSON_SHAPE"
    for key in ("Error Message", "Note", "Information"):
        value = body.get(key)
        if value:
            return f"{key}: {str(value)[:240]}"
    return None


def _request(api_key: str, function: str, timeout: int = 20, **params) -> dict:
    import requests

    if not api_key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY_MISSING")
    response = requests.get(BASE_URL, params={"function": function, "apikey": api_key, **params}, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    error = _api_error(body)
    if error:
        raise RuntimeError(f"ALPHA_VANTAGE_API_ERROR: {error}")
    return body


def health_check(api_key: str) -> dict:
    body = _request(api_key, "MARKET_STATUS")
    markets = body.get("markets")
    if not isinstance(markets, list) or not markets:
        raise RuntimeError("ALPHA_VANTAGE_HEALTH_EMPTY")
    return {"ok": True, "markets": len(markets)}


def _country_tokens(country: str | None, isin: str | None) -> set[str]:
    raw = str(country or "").strip().lower()
    if raw:
        tokens = {raw}
        if len(raw) == 2:
            tokens |= COUNTRY_ALIASES.get(raw.upper(), set())
        return tokens
    prefix = str(isin or "")[:2].upper()
    return COUNTRY_ALIASES.get(prefix, set())


def _pick_search_match(matches: list[dict], name: str, country: str | None, isin: str | None,
                       min_match_score: float = 0.70) -> tuple[dict | None, str]:
    tokens = _country_tokens(country, isin)
    candidates = []
    for row in matches:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("1. symbol") or "").strip()
        row_name = str(row.get("2. name") or "").strip()
        kind = str(row.get("3. type") or "").strip().lower()
        region = str(row.get("4. region") or "").strip()
        try:
            score = float(row.get("9. matchScore") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if not symbol or score < min_match_score:
            continue
        if kind and not any(token in kind for token in ("equity", "stock")):
            continue
        region_lower = region.lower()
        if tokens and not any(token in region_lower for token in tokens):
            continue
        candidates.append((score, symbol, row_name, region, row))
    if not candidates:
        return None, "NO_CONFIDENT_COUNTRY_MATCH"
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.03 and candidates[0][1] != candidates[1][1]:
        return None, "AMBIGUOUS_MATCH"
    return candidates[0][4], ""


def _load_cache(path: str | Path | None) -> dict[str, dict]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        return {row.get("isin", ""): row for row in csv.DictReader(fh, delimiter=";") if row.get("isin")}


def _save_cache(path: str | Path | None, cache: dict[str, dict]) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CACHE_FIELDS, delimiter=";")
        writer.writeheader()
        for isin in sorted(cache):
            writer.writerow({k: cache[isin].get(k, "") for k in CACHE_FIELDS})


def _fresh(row: dict, positive_ttl_days: int, negative_ttl_days: int) -> bool:
    ttl = positive_ttl_days if str(row.get("status") or "").upper() == "RESOLVED" else negative_ttl_days
    try:
        stamp = datetime.fromisoformat(str(row.get("updated_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp >= datetime.now(timezone.utc) - timedelta(days=max(1, int(ttl)))
    except Exception:
        return False


def resolve_symbol(security: dict, api_key: str, cache_path: str | Path | None = None,
                   min_match_score: float = 0.70, positive_ttl_days: int = 90,
                   negative_ttl_days: int = 30) -> AlphaResolutionResult:
    isin = str(security.get("isin") or "").strip().upper()
    yahoo_ticker = str(security.get("yahoo_ticker") or "").strip()
    name = str(security.get("name") or "").strip()
    country = str(security.get("country") or "").strip()
    if not isin or not name:
        return AlphaResolutionResult(None, "INPUT", reason="INSUFFICIENT_IDENTITY")

    cache = _load_cache(cache_path)
    old = cache.get(isin, {})
    same_identity = (
        old
        and str(old.get("yahoo_ticker") or "").upper() == yahoo_ticker.upper()
        and str(old.get("name") or "").strip().upper() == name.upper()
    )
    if same_identity and _fresh(old, positive_ttl_days, negative_ttl_days):
        if str(old.get("status") or "").upper() == "RESOLVED" and old.get("alpha_symbol"):
            return AlphaResolutionResult(str(old["alpha_symbol"]), "CACHE", str(old.get("region") or ""),
                                         float(old.get("match_score") or 0), 0)
        return AlphaResolutionResult(None, "CACHE", api_calls=0, reason=str(old.get("status") or "CACHED_NEGATIVE"))

    body = _request(api_key, "SYMBOL_SEARCH", keywords=name)
    best, reason = _pick_search_match(body.get("bestMatches") or [], name, country, isin, min_match_score)
    now = datetime.now(timezone.utc).isoformat()
    if best:
        symbol = str(best.get("1. symbol") or "").strip()
        region = str(best.get("4. region") or "").strip()
        score = float(best.get("9. matchScore") or 0)
        cache[isin] = {"isin": isin, "yahoo_ticker": yahoo_ticker, "name": name, "country": country,
                       "alpha_symbol": symbol, "region": region, "match_score": str(score),
                       "status": "RESOLVED", "updated_at": now}
        _save_cache(cache_path, cache)
        return AlphaResolutionResult(symbol, "API", region, score, 1)

    cache[isin] = {"isin": isin, "yahoo_ticker": yahoo_ticker, "name": name, "country": country,
                   "alpha_symbol": "", "region": "", "match_score": "", "status": reason,
                   "updated_at": now}
    _save_cache(cache_path, cache)
    return AlphaResolutionResult(None, "API", api_calls=1, reason=reason)


def fetch_overview(symbol: str, api_key: str) -> list[dict]:
    body = _request(api_key, "OVERVIEW", symbol=symbol)
    if not body or str(body.get("Symbol") or "").strip().upper() != str(symbol).strip().upper():
        raise RuntimeError("ALPHA_VANTAGE_OVERVIEW_IDENTITY_MISMATCH")
    observations = []
    for source_field, target_field in OVERVIEW_FIELDS.items():
        raw = body.get(source_field)
        if raw in (None, "", "None", "-"):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        observations.append({"field": target_field, "value": value})
    if not observations:
        raise RuntimeError("ALPHA_VANTAGE_OVERVIEW_NO_USABLE_FIELDS")
    return observations


def resolve_and_fetch_overview(security: dict, api_key: str, cache_path: str | Path | None = None,
                               delay_seconds: float = 0.2, **resolver_kwargs) -> tuple[list[dict], dict]:
    resolution = resolve_symbol(security, api_key, cache_path=cache_path, **resolver_kwargs)
    meta = {"symbol": resolution.symbol, "resolution_source": resolution.source,
            "resolution_api_calls": resolution.api_calls, "reason": resolution.reason,
            "region": resolution.region, "match_score": resolution.match_score,
            "overview_api_calls": 0}
    if not resolution.symbol:
        return [], meta
    if delay_seconds:
        time.sleep(delay_seconds)
    fields = fetch_overview(resolution.symbol, api_key)
    meta["overview_api_calls"] = 1
    return fields, meta
