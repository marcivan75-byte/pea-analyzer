from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
import csv
import re
import time
import unicodedata

BASE_URL = "https://www.alphavantage.co/query"
CACHE_FIELDS = ["isin", "yahoo_ticker", "name", "country", "alpha_symbol", "region", "match_score", "status", "updated_at"]

COUNTRY_ALIASES = {
    "FR": {"france"}, "NL": {"netherlands"}, "BE": {"belgium"}, "DE": {"germany"},
    "IT": {"italy"}, "ES": {"spain"}, "PT": {"portugal"}, "AT": {"austria"},
    "IE": {"ireland"}, "FI": {"finland"}, "SE": {"sweden"}, "DK": {"denmark"},
    "NO": {"norway"}, "LU": {"luxembourg"}, "CH": {"switzerland"}, "GB": {"united kingdom", "uk"},
}
EUROPEAN_PROXY_REGIONS = {
    "frankfurt", "germany", "london", "united kingdom", "paris", "france", "amsterdam",
    "netherlands", "milan", "italy", "madrid", "spain", "brussels", "belgium", "lisbon",
    "portugal", "oslo", "norway", "stockholm", "sweden", "copenhagen", "denmark", "helsinki",
    "finland", "vienna", "austria", "dublin", "ireland", "luxembourg",
}
PROXY_FORBIDDEN_NAME_TOKENS = {"cdr", "adr", "depositary", "hedged", "etf", "warrant", "certificate"}
CORP_WORDS = {"sa", "se", "nv", "plc", "ag", "spa", "ab", "asa", "oyj", "group", "groupe", "holding", "holdings", "company"}


@dataclass(frozen=True)
class AlphaResolutionResult:
    symbol: str | None
    source: str
    region: str = ""
    match_score: float = 0.0
    api_calls: int = 0
    reason: str = ""


@dataclass(frozen=True)
class AlphaHistoryResult:
    frames: dict[str, object]
    failures: list[dict]
    api_calls: int


def _api_error(body) -> str | None:
    if not isinstance(body, dict):
        return "INVALID_JSON_SHAPE"
    for key in ("Error Message", "Note", "Information"):
        value = body.get(key)
        if value:
            return f"{key}: {str(value)[:240]}"
    return None


def _is_per_second_throttle(error: str | None) -> bool:
    text = str(error or "").lower()
    return (
        "1 request per second" in text
        or "spreading out your free api requests" in text
        or "spread out your free api requests" in text
    )


def _request(
    api_key: str,
    function: str,
    timeout: int = 20,
    max_rate_retries: int = 2,
    rate_retry_wait_seconds: float = 1.15,
    **params,
) -> dict:
    import requests

    if not api_key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY_MISSING")
    for attempt in range(max(0, int(max_rate_retries)) + 1):
        response = requests.get(
            BASE_URL,
            params={"function": function, "apikey": api_key, **params},
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        error = _api_error(body)
        if not error:
            return body
        if _is_per_second_throttle(error) and attempt < max_rate_retries:
            time.sleep(max(1.05, float(rate_retry_wait_seconds)))
            continue
        raise RuntimeError(f"ALPHA_VANTAGE_API_ERROR: {error}")
    raise RuntimeError("ALPHA_VANTAGE_API_RETRY_EXHAUSTED")


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


def _name_key(value: object) -> str:
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    tokens = [t for t in re.sub(r"[^a-z0-9]+", " ", s).split() if t not in CORP_WORDS]
    return " ".join(tokens)


def _name_similarity(a: object, b: object) -> float:
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    if (ka in kb or kb in ka) and min(len(ka), len(kb)) >= 4:
        return 0.93
    return SequenceMatcher(None, ka, kb).ratio()


def _pick_entity_proxy(matches: list[dict], name: str) -> tuple[dict | None, float]:
    """Select only a EUR-denominated European secondary listing of the same issuer.

    The proxy is never allowed to stand in for absolute per-share values. It only opens the door
    to consolidated, dimensionless issuer metrics in the higher-level free-capture adapter.
    """
    candidates: list[tuple[float, float, str, dict]] = []
    for row in matches:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("1. symbol") or "").strip()
        row_name = str(row.get("2. name") or "").strip()
        kind = str(row.get("3. type") or "").strip().lower()
        region = str(row.get("4. region") or "").strip().lower()
        currency = str(row.get("8. currency") or "").strip().upper()
        if not symbol or (kind and not any(t in kind for t in ("equity", "stock"))):
            continue
        if currency != "EUR" or region not in EUROPEAN_PROXY_REGIONS:
            continue
        name_tokens = set(_name_key(row_name).split())
        if name_tokens & PROXY_FORBIDDEN_NAME_TOKENS:
            continue
        similarity = _name_similarity(name, row_name)
        if similarity < 0.88:
            continue
        try:
            provider_score = float(row.get("9. matchScore") or 0)
        except (TypeError, ValueError):
            provider_score = 0.0
        candidates.append((similarity, provider_score, symbol, row))
    if not candidates:
        return None, 0.0
    candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return candidates[0][3], candidates[0][0]


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


def _search_preview(matches: list[dict], limit: int = 4) -> str:
    items: list[str] = []
    for row in matches:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("1. symbol") or "").strip()
        name = str(row.get("2. name") or "").strip().replace("|", "/")
        kind = str(row.get("3. type") or "").strip()
        region = str(row.get("4. region") or "").strip()
        currency = str(row.get("8. currency") or "").strip()
        score = str(row.get("9. matchScore") or "").strip()
        if not symbol:
            continue
        items.append(f"{symbol}~{name[:42]}~{kind}~{region}~{currency}~{score}")
        if len(items) >= limit:
            break
    return "|".join(items)


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
    status = str(row.get("status") or "").upper()
    ttl = positive_ttl_days if status.startswith("RESOLVED") else negative_ttl_days
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
        status = str(old.get("status") or "").upper()
        if status.startswith("RESOLVED") and old.get("alpha_symbol"):
            source = "CACHE_PROXY" if status == "RESOLVED_ENTITY_PROXY" else "CACHE"
            return AlphaResolutionResult(str(old["alpha_symbol"]), source, str(old.get("region") or ""),
                                         float(old.get("match_score") or 0), 0, reason=status)
        return AlphaResolutionResult(None, "CACHE", api_calls=0, reason=str(old.get("status") or "CACHED_NEGATIVE"))

    body = _request(api_key, "SYMBOL_SEARCH", keywords=name)
    matches = body.get("bestMatches") or []
    best, reason = _pick_search_match(matches, name, country, isin, min_match_score)
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

    proxy, similarity = _pick_entity_proxy(matches, name)
    if proxy:
        symbol = str(proxy.get("1. symbol") or "").strip()
        region = str(proxy.get("4. region") or "").strip()
        cache[isin] = {"isin": isin, "yahoo_ticker": yahoo_ticker, "name": name, "country": country,
                       "alpha_symbol": symbol, "region": region, "match_score": str(similarity),
                       "status": "RESOLVED_ENTITY_PROXY", "updated_at": now}
        _save_cache(cache_path, cache)
        return AlphaResolutionResult(symbol, "API_PROXY", region, similarity, 1, reason="EUR_ENTITY_PROXY")

    preview = _search_preview(matches)
    audited_reason = f"{reason}::{preview}" if preview else reason
    cache[isin] = {"isin": isin, "yahoo_ticker": yahoo_ticker, "name": name, "country": country,
                   "alpha_symbol": "", "region": "", "match_score": "", "status": audited_reason,
                   "updated_at": now}
    _save_cache(cache_path, cache)
    return AlphaResolutionResult(None, "API", api_calls=1, reason=audited_reason)


def fetch_daily_history(symbol: str, api_key: str, canonical_ticker: str,
                        min_rows: int = 60, outputsize: str = "compact") -> AlphaHistoryResult:
    import pandas as pd

    try:
        body = _request(api_key, "TIME_SERIES_DAILY", symbol=symbol, outputsize=outputsize)
        meta = body.get("Meta Data") if isinstance(body, dict) else None
        returned_symbol = str((meta or {}).get("2. Symbol") or "").strip()
        if returned_symbol and returned_symbol.upper() != str(symbol).strip().upper():
            return AlphaHistoryResult({}, [{"ticker": canonical_ticker, "symbol": symbol,
                                            "reason": "IDENTITY_MISMATCH", "returned_symbol": returned_symbol}], 1)
        series = body.get("Time Series (Daily)") if isinstance(body, dict) else None
        if not isinstance(series, dict) or not series:
            return AlphaHistoryResult({}, [{"ticker": canonical_ticker, "symbol": symbol,
                                            "reason": "NO_DAILY_SERIES"}], 1)
        records = []
        for date, row in series.items():
            if not isinstance(row, dict):
                continue
            records.append({
                "Date": pd.to_datetime(date, utc=True),
                "Open": row.get("1. open"), "High": row.get("2. high"),
                "Low": row.get("3. low"), "Close": row.get("4. close"),
                "Volume": row.get("5. volume"),
            })
        frame = pd.DataFrame(records).drop_duplicates("Date").sort_values("Date").set_index("Date")
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["Close"])
        if len(frame) < int(min_rows):
            return AlphaHistoryResult({}, [{"ticker": canonical_ticker, "symbol": symbol,
                                            "reason": "INSUFFICIENT_ROWS", "rows": len(frame)}], 1)
        return AlphaHistoryResult({canonical_ticker: frame}, [], 1)
    except Exception as exc:
        return AlphaHistoryResult({}, [{"ticker": canonical_ticker, "symbol": symbol,
                                        "reason": type(exc).__name__, "detail": str(exc)[:240]}], 1)


def save_alpha_history_cache(frames: dict[str, object], cache_dir: str | Path,
                             prefix: str = "history_alphavantage") -> str | None:
    import pandas as pd

    if not frames:
        return None
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    combined = pd.concat({ticker: frame for ticker, frame in sorted(frames.items())}, axis=1)
    output = cache / f"{prefix}_00000.parquet"
    combined.to_parquet(output)
    return str(output)
