from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse
import json
import math
import re
from typing import Callable

from bs4 import BeautifulSoup
import pandas as pd

from v182.sources.rate_limit import StartRateLimiter

TRADINGVIEW_BASE = "https://www.tradingview.com"
CACHE_VERSION = "TRADINGVIEW_TECHNICAL_V2"
SIGNAL_SCORE = {"STRONG_SELL": -2, "SELL": -1, "NEUTRAL": 0, "BUY": 1, "STRONG_BUY": 2}

# Yahoo suffix -> TradingView exchange. A URL is attempted only when this
# deterministic mapping exists; name-based search is deliberately forbidden.
YAHOO_SUFFIX_TO_EXCHANGE = {
    "PA": "EURONEXT",
    "AS": "EURONEXT",
    "BR": "EURONEXT",
    "LS": "EURONEXT",
    "IR": "EURONEXT",
    "MC": "BME",
    "MI": "MIL",
    "DE": "XETR",
    "F": "FWB",
    "L": "LSE",
    "OL": "OSL",
    "ST": "OMXSTO",
    "CO": "OMXCOP",
    "HE": "OMXHEX",
    "SW": "SIX",
    "VI": "VIE",
    "AT": "ATHEX",
    "WA": "GPW",
    "PR": "PRA",
}


@dataclass(frozen=True)
class TradingViewResult:
    observations: list[dict]
    failures: list[dict]
    metrics: dict


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_hours(value: object, now: datetime) -> float:
    parsed = _parse_utc(value)
    if parsed is None:
        return math.inf
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def _canon_signal(value: object) -> str | None:
    text = " ".join(str(value or "").strip().upper().replace("_", " ").split())
    return {
        "STRONG SELL": "STRONG_SELL",
        "SELL": "SELL",
        "NEUTRAL": "NEUTRAL",
        "BUY": "BUY",
        "STRONG BUY": "STRONG_BUY",
    }.get(text)


def parse_technical_summary_html(html: str) -> dict[str, object]:
    """Parse TradingView's public Technicals FAQ summary for 1D/1W/1M.

    The page server-renders a factual sentence containing the three ratings.
    Parsing is deliberately fail-closed: all three values must be present in
    that same sentence and belong to TradingView's documented five-state enum.
    """
    try:
        text = " ".join(" ".join(BeautifulSoup(html, "lxml").stripped_strings).split())
    except Exception:
        return {}
    if not text:
        return {}
    state = r"(strong sell|strong buy|sell|buy|neutral)"
    patterns = (
        (
            rf"Our technical rating for .*? is\s+{state}\s+today\."
            rf".*?1 week rating\s+(?:the\s+)?{state}\s+trend is prevailing,"
            rf"\s+and 1 month rating shows\s+(?:the\s+)?{state}\s+signal"
        ),
        (
            rf"Our summary technical rating for .*? is\s+{state}\s+today\."
            rf".*?1[- ]week rating,?\s+(?:the\s+)?{state}\s+trend prevails,"
            rf"\s+and 1 month rating shows\s+(?:the\s+)?{state}\s+signal"
        ),
    )
    match = next(
        (candidate for pattern in patterns if (candidate := re.search(pattern, text, flags=re.IGNORECASE))),
        None,
    )
    if not match:
        return {}
    daily, weekly, monthly = (_canon_signal(value) for value in match.groups())
    if not all((daily, weekly, monthly)):
        return {}
    fields: dict[str, object] = {"tradingview_technical_complete": True}
    for timeframe, signal in (("daily", daily), ("weekly", weekly), ("monthly", monthly)):
        fields[f"tradingview_{timeframe}_signal"] = signal
        fields[f"tradingview_{timeframe}_score"] = SIGNAL_SCORE[signal]
    return fields


def tradingview_symbol(row: object) -> tuple[str, str] | None:
    getter = row.get if hasattr(row, "get") else lambda key, default=None: default
    explicit = str(getter("tradingview_symbol", "") or "").strip().upper()
    if explicit and re.fullmatch(r"[A-Z0-9_]+:[A-Z0-9._-]+", explicit):
        exchange, ticker = explicit.split(":", 1)
        return exchange, ticker
    yahoo = str(getter("yahoo_ticker", "") or "").strip().upper()
    if "." not in yahoo:
        return None
    ticker, suffix = yahoo.rsplit(".", 1)
    exchange = YAHOO_SUFFIX_TO_EXCHANGE.get(suffix)
    if not exchange or not re.fullmatch(r"[A-Z0-9._-]{1,40}", ticker):
        return None
    if exchange in {"OMXSTO", "OMXCOP", "OMXHEX"}:
        ticker = ticker.replace("-", "_")
    return exchange, ticker


def technical_url(row: object) -> tuple[str, str] | None:
    symbol = tradingview_symbol(row)
    if symbol is None:
        return None
    exchange, ticker = symbol
    return f"{TRADINGVIEW_BASE}/symbols/{exchange}-{ticker}/technicals/", f"{exchange}:{ticker}"


def _load(path: Path) -> dict:
    if not path.exists():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": CACHE_VERSION, "entries": {}}
    if data.get("version") != CACHE_VERSION or not isinstance(data.get("entries"), dict):
        return {"version": CACHE_VERSION, "entries": {}}
    return data


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _default_fetcher(url: str, *, timeout: float):
    import requests

    return requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/V4; selected-public-context)",
            "Accept-Language": "en-US,en;q=0.8",
        },
        timeout=timeout,
        allow_redirects=True,
    )


def horizon_signal(fields: dict[str, object], horizon: str) -> tuple[object | None, object | None]:
    timeframe = {"TCT": "daily", "CT": "weekly", "MT": "monthly"}.get(str(horizon or "").upper())
    if timeframe is None:
        return None, None
    return fields.get(f"tradingview_{timeframe}_signal"), fields.get(f"tradingview_{timeframe}_score")


def collect_technical_context_cached(
    rows: pd.DataFrame,
    cache_path: str | Path,
    *,
    refresh_budget: int = 40,
    ttl_hours: float = 6.0,
    request_start_interval_seconds: float = 1.0,
    timeout_seconds: float = 20.0,
    max_workers: int = 4,
    fetcher: Callable[..., object] | None = None,
    now: datetime | None = None,
) -> TradingViewResult:
    current = (now or _now_utc()).astimezone(timezone.utc)
    cache_file = Path(cache_path)
    cache = _load(cache_file)
    fetch = fetcher or _default_fetcher
    limiter = StartRateLimiter(request_start_interval_seconds)
    failures: list[dict] = []
    unique = rows.drop_duplicates("isin").copy() if "isin" in rows else pd.DataFrame()
    due: list[tuple[str, object]] = []
    cache_hits = 0
    identity_mismatch_rejected = 0
    stale_rejected = 0
    unresolved = 0
    for _, row in unique.iterrows():
        isin = str(row.get("isin") or "").strip()
        if not isin:
            continue
        resolved = technical_url(row)
        if resolved is None:
            unresolved += 1
            failures.append({"isin": isin, "source": "TradingView", "reason": "NO_DETERMINISTIC_EXCHANGE_TICKER"})
            cache["entries"].pop(isin, None)
            continue
        _, expected_symbol = resolved
        entry = cache["entries"].get(isin)
        fields = dict(entry.get("fields") or {}) if isinstance(entry, dict) else {}
        identity_valid = bool(
            entry
            and entry.get("validated_isin") == isin
            and entry.get("symbol") == expected_symbol
        )
        complete = bool(
            fields.get("tradingview_technical_complete") is True
            and all(fields.get(f"tradingview_{timeframe}_signal") in SIGNAL_SCORE for timeframe in ("daily", "weekly", "monthly"))
            and re.fullmatch(r"[0-9a-f]{64}", str(entry.get("page_sha256") or ""))
        )
        fresh = bool(entry and _age_hours(entry.get("fetched_at_utc"), current) <= ttl_hours)
        if identity_valid and complete and fresh:
            cache_hits += 1
        else:
            if entry and not identity_valid:
                identity_mismatch_rejected += 1
            if entry and not fresh:
                stale_rejected += 1
            # A stale or differently bound entry must never survive a failed refresh.
            cache["entries"].pop(isin, None)
            due.append((isin, row))
    due = due[: max(0, int(refresh_budget))]

    def worker(item: tuple[str, object]) -> tuple[str, dict | None, dict | None]:
        isin, row = item
        resolved = technical_url(row)
        if resolved is None:
            return isin, None, {"isin": isin, "source": "TradingView", "reason": "NO_DETERMINISTIC_EXCHANGE_TICKER"}
        url, symbol = resolved
        try:
            limiter.wait()
            response = fetch(url, timeout=timeout_seconds)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            html = str(getattr(response, "text", "") or "")
            final_url = str(getattr(response, "url", url) or url)
            parsed_final = urlparse(final_url)
            parsed_expected = urlparse(url)
            if (
                parsed_final.hostname not in {"tradingview.com", "www.tradingview.com"}
                or parsed_final.path.rstrip("/") != parsed_expected.path.rstrip("/")
            ):
                return isin, None, {
                    "isin": isin,
                    "source": "TradingView",
                    "reason": "UNEXPECTED_FINAL_URL",
                    "symbol": symbol,
                    "url": final_url,
                }
            # The public page must prove the exact exchange-qualified symbol.
            proof = f"/chart/?symbol={symbol}"
            identity_markers = (
                proof,
                f'"pro_symbol":"{symbol}"',
                f'"resolved_symbol":"{symbol}"',
                f'"primary_name":"{symbol}"',
            )
            if not any(marker in html for marker in identity_markers):
                return isin, None, {"isin": isin, "source": "TradingView", "reason": "SYMBOL_IDENTITY_NOT_PROVEN", "symbol": symbol, "url": final_url}
            fields = parse_technical_summary_html(html)
            if not fields:
                return isin, None, {"isin": isin, "source": "TradingView", "reason": "NO_COMPLETE_1D_1W_1M_SUMMARY", "symbol": symbol, "url": final_url}
            return isin, {
                "fetched_at_utc": current.isoformat(),
                "source_url": final_url,
                "symbol": symbol,
                "validated_isin": isin,
                "identity_proof": proof,
                "fields": fields,
                "page_sha256": sha256(html.encode("utf-8", errors="replace")).hexdigest(),
            }, None
        except Exception as exc:
            return isin, None, {"isin": isin, "source": "TradingView", "reason": type(exc).__name__, "detail": str(exc)[:180], "symbol": symbol, "url": url}

    success = 0
    workers = max(1, min(int(max_workers), len(due))) if due else 0
    if workers:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tradingview-selected") as pool:
            futures = [pool.submit(worker, item) for item in due]
            for future in as_completed(futures):
                isin, entry, failure = future.result()
                if failure:
                    failures.append(failure)
                else:
                    cache["entries"][isin] = entry
                    success += 1

    cache["updated_at_utc"] = current.isoformat()
    cache["policy"] = {
        "selected_only": True,
        "refresh_budget": int(refresh_budget),
        "ttl_hours": float(ttl_hours),
        "request_start_interval_seconds": float(request_start_interval_seconds),
        "max_workers": int(max_workers),
        "raw_html_persisted": False,
        "identity": "ISIN_MASTER_TO_EXCHANGE_QUALIFIED_SYMBOL_WITH_PAGE_PROOF",
        "decision_influence": False,
    }
    _save(cache_file, cache)

    observations: list[dict] = []
    usable = 0
    for _, row in rows.iterrows():
        isin = str(row.get("isin") or "").strip()
        entry = cache["entries"].get(isin)
        resolved = technical_url(row)
        expected_symbol = resolved[1] if resolved is not None else None
        if (
            not entry
            or entry.get("validated_isin") != isin
            or entry.get("symbol") != expected_symbol
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("page_sha256") or ""))
        ):
            continue
        if _age_hours(entry.get("fetched_at_utc"), current) > ttl_hours:
            continue
        fields = dict(entry.get("fields") or {})
        if not (
            fields.get("tradingview_technical_complete") is True
            and all(fields.get(f"tradingview_{timeframe}_signal") in SIGNAL_SCORE for timeframe in ("daily", "weekly", "monthly"))
        ):
            continue
        usable += 1
        signal, score = horizon_signal(fields, str(row.get("horizon") or ""))
        if signal is not None:
            fields["tradingview_horizon_signal"] = signal
            fields["tradingview_horizon_score"] = score
        fields["tradingview_source_url"] = entry.get("source_url")
        fields["tradingview_symbol"] = entry.get("symbol")
        fields["tradingview_collected_at_utc"] = entry.get("fetched_at_utc")
        fields["tradingview_page_sha256"] = entry.get("page_sha256")
        for field, value in fields.items():
            observations.append({
                "isin": isin,
                "asset_class": str(row.get("asset_class") or ""),
                "horizon": str(row.get("horizon") or ""),
                "field": field,
                "value": value,
                "source": "TradingView public Technicals page",
                "source_url": entry.get("source_url"),
                "collected_at": entry.get("fetched_at_utc"),
                "validation_status": "EXCHANGE_QUALIFIED_SYMBOL_PROVEN",
            })

    return TradingViewResult(
        observations=observations,
        failures=failures,
        metrics={
            "requested_rows": int(len(rows)),
            "unique_instruments": int(len(unique)),
            "live_refresh_requested": int(len(due)),
            "live_refresh_success": int(success),
            "cache_hits": int(cache_hits),
            "identity_mismatch_rejected": int(identity_mismatch_rejected),
            "stale_rejected": int(stale_rejected),
            "unresolved_identity": int(unresolved),
            "usable_rows": int(usable),
            "observations": int(len(observations)),
            "selected_only": True,
            "decision_influence": False,
            "score_influence": 0.0,
            "identity_fail_closed": True,
            "raw_html_persisted": False,
        },
    )

