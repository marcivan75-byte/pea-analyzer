from __future__ import annotations

from difflib import SequenceMatcher
import re
import time
from typing import Any

import requests

API_BASE = "https://api.parse.bot/scraper/060a9926-db20-4c1e-8dc7-26d52b79ee8e"


class MarketBeatParseError(RuntimeError):
    pass


def _norm_name(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    stop = {"sa", "se", "nv", "plc", "spa", "ag", "group", "holding", "holdings"}
    return " ".join(token for token in text.split() if token not in stop)


class MarketBeatParseClient:
    """Gated Parse/MarketBeat client for issuer-level analyst intelligence only."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 20.0,
        min_interval_seconds: float = 12.2,
        session: requests.Session | None = None,
    ):
        if not api_key:
            raise ValueError("PARSE_API_KEY is required")
        self.api_key = api_key
        self.timeout = timeout
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.session = session or requests.Session()
        self._last_request_at = 0.0

    def _get(self, endpoint: str, **params) -> dict:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        response = self.session.get(
            f"{API_BASE}/{endpoint}",
            params={k: v for k, v in params.items() if v not in (None, "")},
            headers={"X-API-Key": self.api_key},
            timeout=self.timeout,
        )
        self._last_request_at = time.monotonic()
        if response.status_code == 429:
            raise MarketBeatParseError("PARSE_RATE_LIMIT")
        response.raise_for_status()
        payload = response.json()
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

    def resolve_issuer(self, name: str, min_score: float = 0.72) -> dict | None:
        expected = _norm_name(name)
        if not expected:
            return None
        candidates = self.search_stocks(name)
        best = None
        best_score = 0.0
        for item in candidates:
            candidate = _norm_name(item.get("name"))
            score = SequenceMatcher(None, expected, candidate).ratio() if candidate else 0.0
            if score > best_score:
                best = item
                best_score = score
        if best is None or best_score < min_score:
            return None
        return {**best, "_match_score": round(best_score, 4)}

    def get_stock_forecast(self, ticker: str, exchange: str | None = None) -> dict:
        return self._get("get_stock_forecast", ticker=ticker, exchange=exchange)

    def get_analyst_ratings(self, ticker: str, exchange: str | None = None) -> dict:
        return self._get("get_analyst_ratings", ticker=ticker, exchange=exchange)


def forecast_breakdown(payload: dict) -> list[dict]:
    """Preserve provider period rows until a live smoke locks the exact schema."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    rows = data.get("consensus_rating_breakdown", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
