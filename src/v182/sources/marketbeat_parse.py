from __future__ import annotations

from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
import csv
import re
import time
from typing import Any

import requests

API_BASE = "https://api.parse.bot/scraper/060a9926-db20-4c1e-8dc7-26d52b79ee8e"
US_ANALYST_EXCHANGES = {"NASDAQ", "NYSE", "NYSEAMERICAN", "AMEX"}
MAP_FIELDS = [
    "isin", "yahoo_ticker", "name", "marketbeat_ticker", "marketbeat_exchange",
    "local_marketbeat_ticker", "local_marketbeat_exchange", "match_type",
    "match_score", "status", "updated_at",
]


class MarketBeatParseError(RuntimeError):
    pass


def _norm_name(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    stop = {"sa", "se", "nv", "plc", "spa", "ag", "group", "groupe", "holding", "holdings"}
    return " ".join(token for token in text.split() if token not in stop)


def _ticker_base(value: Any) -> str:
    return str(value or "").strip().upper().split(".", 1)[0]


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100.0, 4)


def _abs_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 6)


def _parse_count(value: Any) -> int | None:
    match = re.search(r"-?\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _parse_money(value: Any) -> tuple[float | None, str | None]:
    text = str(value or "").strip()
    currency = "USD" if "$" in text else "EUR" if "€" in text else "GBP" if "£" in text else None
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None, currency
    try:
        return float(match.group(0).replace(",", "")), currency
    except ValueError:
        return None, currency


def _label_from_score(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 87.5:
        return "STRONG_BUY"
    if score >= 62.5:
        return "BUY"
    if score >= 37.5:
        return "HOLD"
    if score >= 12.5:
        return "SELL"
    return "STRONG_SELL"


def _score_from_counts(counts: dict[str, int | None]) -> float | None:
    clean = {key: max(0, int(value or 0)) for key, value in counts.items()}
    total = sum(clean.values())
    if total <= 0:
        return None
    weighted = (
        clean.get("strong_buy", 0) * 100.0
        + clean.get("buy", 0) * 75.0
        + clean.get("hold", 0) * 50.0
        + clean.get("sell", 0) * 25.0
    )
    return round(weighted / total, 4)


def _period_value(row: dict, prefix: str) -> Any:
    for key, value in row.items():
        if str(key).startswith(prefix):
            return value
    return None


class MarketBeatParseClient:
    """Parse/MarketBeat client for issuer-level analyst intelligence only.

    MarketBeat data may come from an ADR or US secondary listing. This module
    never supplies a local PEA price; it only supplies issuer-level analyst
    sentiment and target-revision information.
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 20.0,
        min_interval_seconds: float = 12.2,
        session: requests.Session | None = None,
    ):
        if not api_key:
            raise ValueError("MARKETBEAT_API_KEY is required")
        self.api_key = api_key
        self.timeout = timeout
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.session = session or requests.Session()
        self._last_request_at = 0.0
        self.api_calls = 0

    def _get(self, endpoint: str, **params) -> dict:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        try:
            response = self.session.get(
                f"{API_BASE}/{endpoint}",
                params={k: v for k, v in params.items() if v not in (None, "")},
                headers={"X-API-Key": self.api_key, "User-Agent": "V18.2-MarketBeat/1.0"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MarketBeatParseError(type(exc).__name__) from exc
        finally:
            self._last_request_at = time.monotonic()
        self.api_calls += 1
        if response.status_code == 429:
            raise MarketBeatParseError("PARSE_RATE_LIMIT")
        if response.status_code in {401, 403}:
            raise MarketBeatParseError(f"PARSE_AUTH_{response.status_code}")
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise MarketBeatParseError(f"HTTP_{response.status_code}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketBeatParseError("PARSE_INVALID_JSON") from exc
        if not isinstance(payload, dict):
            raise MarketBeatParseError("PARSE_INVALID_JSON_SHAPE")
        if str(payload.get("status", "")).lower() not in {"", "success"}:
            raise MarketBeatParseError(f"PARSE_STATUS_{payload.get('status')}")
        return payload

    def search_stocks(self, query: str) -> list[dict]:
        payload = self._get("search_stocks", query=query)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        items = data.get("items", []) if isinstance(data, dict) else []
        return [item for item in items if isinstance(item, dict)]

    def resolve_issuer_listing(
        self,
        name: str,
        yahoo_ticker: str,
        *,
        min_score: float = 0.88,
    ) -> dict | None:
        expected = _norm_name(name)
        local_base = _ticker_base(yahoo_ticker)
        if not expected or not local_base:
            return None
        candidates = self.search_stocks(name)
        scored: list[tuple[float, dict]] = []
        for item in candidates:
            candidate = _norm_name(item.get("name"))
            score = SequenceMatcher(None, expected, candidate).ratio() if candidate else 0.0
            if score >= min_score:
                scored.append((score, item))
        if not scored:
            return None

        # Require a same-issuer candidate carrying the local ticker base before
        # accepting a US analyst proxy. This prevents a name-only ADR mismatch.
        local_matches = [
            (score, item) for score, item in scored
            if _ticker_base(item.get("ticker")) == local_base
        ]
        if not local_matches:
            return None
        local_score, local_item = max(local_matches, key=lambda pair: pair[0])
        local_name = _norm_name(local_item.get("name"))

        us_matches = [
            (score, item) for score, item in scored
            if str(item.get("exchange") or "").upper() in US_ANALYST_EXCHANGES
            and _norm_name(item.get("name")) == local_name
        ]
        if us_matches:
            selected_score, selected = max(us_matches, key=lambda pair: pair[0])
            match_type = "US_ANALYST_PROXY"
        else:
            selected_score, selected = local_score, local_item
            match_type = "PRIMARY_OR_LOCAL_LISTING"

        return {
            "marketbeat_ticker": str(selected.get("ticker") or "").strip(),
            "marketbeat_exchange": str(selected.get("exchange") or "").strip(),
            "local_marketbeat_ticker": str(local_item.get("ticker") or "").strip(),
            "local_marketbeat_exchange": str(local_item.get("exchange") or "").strip(),
            "match_type": match_type,
            "match_score": round(min(local_score, selected_score), 4),
        }

    def get_stock_forecast(self, ticker: str, exchange: str | None = None) -> dict:
        return self._get("get_stock_forecast", ticker=ticker, exchange=exchange)

    def get_analyst_ratings(self, ticker: str, exchange: str | None = None) -> dict:
        return self._get("get_analyst_ratings", ticker=ticker, exchange=exchange)


def forecast_breakdown(payload: dict) -> list[dict]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    rows = data.get("consensus_rating_breakdown", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def parse_forecast(payload: dict) -> dict[str, Any]:
    """Normalize MarketBeat forecast periods validated by the live schema smoke."""
    rows = forecast_breakdown(payload)
    if not rows:
        return {}
    period_prefixes = {
        "current": "Current Forecast",
        "1m": "1 Month Ago",
        "3m": "3 Months Ago",
        "12m": "1 Year Ago",
    }
    by_period: dict[str, dict[str, Any]] = {
        period: {"counts": {"strong_buy": None, "buy": None, "hold": None, "sell": None, "strong_sell": None}}
        for period in period_prefixes
    }
    currency: str | None = None

    type_map = {
        "strong buy": "strong_buy",
        "buy": "buy",
        "hold": "hold",
        "sell": "sell",
        "strong sell": "strong_sell",
    }
    for row in rows:
        row_type = str(row.get("Type") or "").strip().casefold()
        for period, prefix in period_prefixes.items():
            raw = _period_value(row, prefix)
            if row_type in type_map:
                by_period[period]["counts"][type_map[row_type]] = _parse_count(raw)
            elif row_type == "consensus price target":
                target, detected_currency = _parse_money(raw)
                by_period[period]["target"] = target
                currency = currency or detected_currency

    for period in period_prefixes:
        counts = by_period[period]["counts"]
        score = _score_from_counts(counts)
        by_period[period]["score"] = score
        by_period[period]["rating"] = _label_from_score(score)
        by_period[period]["n_analysts"] = sum(max(0, int(value or 0)) for value in counts.values()) or None

    current = by_period["current"]
    if current.get("target") is None and current.get("score") is None:
        return {}

    fields: dict[str, Any] = {
        "mb_strong_buy_n": current["counts"].get("strong_buy"),
        "mb_buy_n": current["counts"].get("buy"),
        "mb_hold_n": current["counts"].get("hold"),
        "mb_sell_n": current["counts"].get("sell"),
        "mb_strong_sell_n": current["counts"].get("strong_sell"),
        "mb_n_analysts": current.get("n_analysts"),
        "mb_consensus_score_100": current.get("score"),
        "mb_consensus_rating": current.get("rating"),
        "mb_target_price": current.get("target"),
        "mb_target_currency": currency,
        "mb_target_1m_ago": by_period["1m"].get("target"),
        "mb_target_3m_ago": by_period["3m"].get("target"),
        "mb_target_12m_ago": by_period["12m"].get("target"),
        "mb_consensus_score_1m_ago": by_period["1m"].get("score"),
        "mb_consensus_score_3m_ago": by_period["3m"].get("score"),
        "mb_consensus_score_12m_ago": by_period["12m"].get("score"),
    }
    for horizon in ("1m", "3m", "12m"):
        previous_target = by_period[horizon].get("target")
        previous_score = by_period[horizon].get("score")
        fields[f"mb_target_change_{horizon}_abs"] = _abs_change(current.get("target"), previous_target)
        fields[f"mb_target_change_{horizon}_pct"] = _pct_change(current.get("target"), previous_target)
        fields[f"mb_consensus_delta_{horizon}"] = _abs_change(current.get("score"), previous_score)
    return fields


def _load_map(path: str | Path | None) -> dict[str, dict]:
    if not path:
        return {}
    file = Path(path)
    if not file.exists():
        return {}
    with file.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["isin"]: row for row in csv.DictReader(handle, delimiter=";") if row.get("isin")}


def _save_map(path: str | Path | None, cache: dict[str, dict]) -> None:
    if not path:
        return
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MAP_FIELDS, delimiter=";")
        writer.writeheader()
        for isin in sorted(cache):
            writer.writerow({field: cache[isin].get(field, "") for field in MAP_FIELDS})


def _cache_fresh(row: dict, ttl_days: int) -> bool:
    try:
        stamp = datetime.fromisoformat(str(row.get("updated_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp >= datetime.now(timezone.utc) - timedelta(days=max(1, ttl_days))
    except Exception:
        return False


def collect_selective_forecasts(
    securities: list[dict],
    api_key: str,
    *,
    mapping_path: str | Path | None = None,
    max_issuers: int = 3,
    mapping_ttl_days: int = 90,
    unresolved_ttl_days: int = 7,
    min_match_score: float = 0.88,
    min_interval_seconds: float = 12.2,
) -> tuple[list[dict], list[dict], dict]:
    """Collect at most a few issuer-level forecasts under the Parse free quota."""
    selected = securities[: max(0, int(max_issuers))]
    cache = _load_map(mapping_path)
    client = MarketBeatParseClient(api_key, min_interval_seconds=min_interval_seconds)
    records: list[dict] = []
    failures: list[dict] = []
    mapped_from_cache = mapped_via_api = 0
    fatal_errors = 0

    for security in selected:
        isin = str(security.get("isin") or "").strip()
        yahoo_ticker = str(security.get("yahoo_ticker") or "").strip()
        name = str(security.get("name") or "").strip()
        if not isin or not yahoo_ticker or not name:
            failures.append({"isin": isin, "reason": "IDENTITY_INCOMPLETE"})
            continue

        cached = cache.get(isin, {})
        same_identity = (
            str(cached.get("yahoo_ticker") or "").upper() == yahoo_ticker.upper()
            and _norm_name(cached.get("name")) == _norm_name(name)
        )
        status = str(cached.get("status") or "").upper()
        ttl = mapping_ttl_days if status == "RESOLVED" else unresolved_ttl_days
        fresh = same_identity and _cache_fresh(cached, ttl)
        mapping: dict | None = None
        if fresh and status == "RESOLVED":
            mapping = cached
            mapped_from_cache += 1
        elif fresh and status == "UNRESOLVED":
            failures.append({"isin": isin, "reason": "MAPPING_UNRESOLVED_CACHED"})
            continue
        else:
            try:
                mapping = client.resolve_issuer_listing(name, yahoo_ticker, min_score=min_match_score)
            except MarketBeatParseError as exc:
                failures.append({"isin": isin, "reason": str(exc), "stage": "SEARCH"})
                fatal_errors += 1
                continue
            now = datetime.now(timezone.utc).isoformat()
            if not mapping:
                cache[isin] = {
                    "isin": isin, "yahoo_ticker": yahoo_ticker, "name": name,
                    "status": "UNRESOLVED", "updated_at": now,
                }
                failures.append({"isin": isin, "reason": "MAPPING_NO_SAFE_MATCH"})
                continue
            mapped_via_api += 1
            cache[isin] = {
                "isin": isin,
                "yahoo_ticker": yahoo_ticker,
                "name": name,
                **mapping,
                "status": "RESOLVED",
                "updated_at": now,
            }
            mapping = cache[isin]

        try:
            payload = client.get_stock_forecast(
                str(mapping.get("marketbeat_ticker") or ""),
                str(mapping.get("marketbeat_exchange") or "") or None,
            )
            fields = parse_forecast(payload)
        except MarketBeatParseError as exc:
            failures.append({"isin": isin, "reason": str(exc), "stage": "FORECAST"})
            fatal_errors += 1
            continue
        if not fields:
            failures.append({"isin": isin, "reason": "NO_FORECAST_DATA", "stage": "FORECAST"})
            continue

        fields.update({
            "marketbeat_ticker": mapping.get("marketbeat_ticker"),
            "marketbeat_exchange": mapping.get("marketbeat_exchange"),
            "marketbeat_match_type": mapping.get("match_type"),
            "marketbeat_match_confidence": mapping.get("match_score"),
            "marketbeat_as_of": datetime.now(timezone.utc).date().isoformat(),
            "mb_data_status": "OK",
        })
        records.append({"isin": isin, "fields": fields})

    _save_map(mapping_path, cache)
    metrics = {
        "selected": len(selected),
        "successful": len(records),
        "failed": len(failures),
        "mapped_from_cache": mapped_from_cache,
        "mapped_via_api": mapped_via_api,
        "api_calls": client.api_calls,
        "fatal_errors": fatal_errors,
        "key_present": bool(api_key),
        "success": fatal_errors == 0,
    }
    return records, failures, metrics
