from __future__ import annotations
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
import csv
import os
import re
import time

FINNHUB_BASE = "https://api.finnhub.io/api/v1"
_SCORE_WEIGHTS = {"strongBuy": 5, "buy": 4, "hold": 3, "sell": 2, "strongSell": 1}
_LAST_REQUEST_MONOTONIC = 0.0


def _label_from_score(score: float) -> str:
    if score >= 4.5:
        return "STRONG_BUY"
    if score >= 3.5:
        return "BUY"
    if score >= 2.5:
        return "HOLD"
    if score >= 1.5:
        return "SELL"
    return "STRONG_SELL"


def _ticker_base(value: str | None) -> str:
    return str(value or "").upper().split(".", 1)[0].strip()


def _norm_name(value: str | None) -> str:
    text = str(value or "").upper()
    text = re.sub(r"\b(SA|SE|NV|PLC|AG|SCA|SAS|GROUP|GROUPE|HOLDING|HOLDINGS)\b", " ", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def _pick_lookup_result(
    results: list[dict],
    yahoo_ticker: str | None,
    name: str | None = None,
    queried_by_isin: bool = False,
    min_score: int = 8,
) -> dict | None:
    """Return a Finnhub symbol only when identity evidence is strong enough."""
    if not results:
        return None
    wanted = str(yahoo_ticker or "").upper().strip()
    wanted_base = _ticker_base(wanted)
    wanted_name = _norm_name(name)

    def score(row: dict) -> tuple[int, str]:
        symbol = str(row.get("symbol") or "").upper().strip()
        display = str(row.get("displaySymbol") or "").upper().strip()
        kind = str(row.get("type") or "").lower()
        description = _norm_name(row.get("description"))
        s = 0
        if wanted and (symbol == wanted or display == wanted):
            s += 20
        if wanted_base and (_ticker_base(symbol) == wanted_base or _ticker_base(display) == wanted_base):
            s += 8
        if wanted and "." in wanted and (symbol.endswith(wanted[wanted.index("."):]) or display.endswith(wanted[wanted.index("."):])):
            s += 4
        if "stock" in kind or "common" in kind or "equity" in kind:
            s += 2
        if wanted_name and description:
            similarity = SequenceMatcher(None, wanted_name, description).ratio()
            if wanted_name in description or description in wanted_name or similarity >= 0.72:
                s += 6
        if queried_by_isin and len(results) == 1:
            s += 6
        return (s, symbol)

    best = max(results, key=score)
    return best if score(best)[0] >= int(min_score) else None


def _load_symbol_cache(path: str | Path | None) -> dict[str, dict]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        return {row["isin"]: row for row in csv.DictReader(fh, delimiter=";") if row.get("isin")}


def _save_symbol_cache(path: str | Path | None, cache: dict[str, dict]) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fields = ["isin", "yahoo_ticker", "finnhub_symbol", "status", "updated_at"]
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        writer.writeheader()
        for isin in sorted(cache):
            writer.writerow({k: cache[isin].get(k, "") for k in fields})


def _cache_fresh(row: dict, resolved_ttl_days: int, unresolved_ttl_days: int) -> bool:
    status = str(row.get("status") or "").upper()
    ttl = resolved_ttl_days if status == "RESOLVED" else unresolved_ttl_days
    try:
        stamp = datetime.fromisoformat(str(row.get("updated_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp >= datetime.now(timezone.utc) - timedelta(days=max(1, int(ttl)))
    except Exception:
        return False


def _pace_finnhub() -> None:
    """Global free-tier guard shared by all Finnhub endpoints in this process."""
    global _LAST_REQUEST_MONOTONIC
    interval = max(0.0, float(os.getenv("FINNHUB_MIN_INTERVAL_SECONDS", os.getenv("V211_FINNHUB_DELAY_SECONDS", "1.05"))) or "1.05"))
    now = time.monotonic()
    wait = interval - (now - _LAST_REQUEST_MONOTONIC)
    if _LAST_REQUEST_MONOTONIC > 0 and wait > 0:
        time.sleep(wait)
    _LAST_REQUEST_MONOTONIC = time.monotonic()


def _get_json(session, path: str, params: dict, max_retries: int = 2, backoff_seconds: float = 2.0):
    for attempt in range(max_retries + 1):
        _pace_finnhub()
        resp = session.get(f"{FINNHUB_BASE}{path}", params=params, timeout=15)
        if resp.status_code == 429 and attempt < max_retries:
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else backoff_seconds * (2 ** attempt)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    return None


def fetch_consensus(
    securities: list[dict] | list[str],
    api_key: str,
    symbol_cache_path: str | Path | None = None,
    delay_seconds: float = 1.05,
    max_retries: int = 2,
    resolved_cache_ttl_days: int = 90,
    unresolved_cache_ttl_days: int = 30,
    lookup_min_score: int = 8,
) -> tuple[list[dict], list[dict]]:
    """Fetch Finnhub analyst consensus with guarded ISIN-based symbol resolution."""
    import requests

    normalized: list[dict] = []
    for item in securities:
        if isinstance(item, str):
            normalized.append({"isin": "", "yahoo_ticker": item, "name": ""})
        else:
            normalized.append({
                "isin": str(item.get("isin") or ""),
                "yahoo_ticker": str(item.get("yahoo_ticker") or ""),
                "name": str(item.get("name") or ""),
            })

    cache = _load_symbol_cache(symbol_cache_path)
    observations: list[dict] = []
    failures: list[dict] = []
    session = requests.Session()

    for security in normalized:
        isin = security["isin"]
        yahoo_ticker = security["yahoo_ticker"]
        cache_key = isin or f"TICKER:{yahoo_ticker}"
        cached = cache.get(cache_key, {})
        same_identity = (
            cached
            and str(cached.get("yahoo_ticker") or "").strip().upper() == yahoo_ticker.strip().upper()
        )
        cached_fresh = same_identity and _cache_fresh(cached, resolved_cache_ttl_days, unresolved_cache_ttl_days)
        cached_status = str(cached.get("status") or "").upper()
        finnhub_symbol = str(cached.get("finnhub_symbol") or "").strip() if cached_fresh and cached_status == "RESOLVED" else ""

        if cached_fresh and cached_status == "UNRESOLVED":
            failures.append({"ticker": yahoo_ticker, "isin": isin, "reason": "SYMBOL_UNRESOLVED_CACHED"})
            continue

        try:
            if not finnhub_symbol:
                query = isin or yahoo_ticker or security["name"]
                lookup = _get_json(session, "/search", {"q": query, "token": api_key}, max_retries=max_retries)
                results = (lookup or {}).get("result", [])
                best = _pick_lookup_result(
                    results,
                    yahoo_ticker,
                    name=security["name"],
                    queried_by_isin=bool(isin),
                    min_score=lookup_min_score,
                )
                if best:
                    finnhub_symbol = str(best.get("symbol") or "").strip()
                    cache[cache_key] = {
                        "isin": isin,
                        "yahoo_ticker": yahoo_ticker,
                        "finnhub_symbol": finnhub_symbol,
                        "status": "RESOLVED",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                else:
                    cache[cache_key] = {
                        "isin": isin,
                        "yahoo_ticker": yahoo_ticker,
                        "finnhub_symbol": "",
                        "status": "UNRESOLVED",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    failures.append({"ticker": yahoo_ticker, "isin": isin, "reason": "SYMBOL_UNRESOLVED_OR_LOW_CONFIDENCE"})
                    if delay_seconds:
                        time.sleep(delay_seconds)
                    continue
                if delay_seconds:
                    time.sleep(delay_seconds)

            reco = _get_json(session, "/stock/recommendation", {"symbol": finnhub_symbol, "token": api_key}, max_retries=max_retries)
            if not reco:
                failures.append({"ticker": yahoo_ticker, "isin": isin, "finnhub_symbol": finnhub_symbol, "reason": "NO_RECOMMENDATION_DATA"})
                if delay_seconds:
                    time.sleep(delay_seconds)
                continue

            latest = reco[0]
            counts = {k: latest.get(k, 0) or 0 for k in _SCORE_WEIGHTS}
            total = sum(counts.values())
            if total == 0:
                failures.append({"ticker": yahoo_ticker, "isin": isin, "finnhub_symbol": finnhub_symbol, "reason": "EMPTY_RECOMMENDATION_COUNTS"})
                if delay_seconds:
                    time.sleep(delay_seconds)
                continue

            score = round(sum(counts[k] * weight for k, weight in _SCORE_WEIGHTS.items()) / total, 2)
            rating = _label_from_score(score)
            fields = {
                "consensus": rating,
                "consensus_rating": rating,
                "consensus_score": score,
                "consensus_period": latest.get("period"),
                "buy_n": counts["strongBuy"] + counts["buy"],
                "hold_n": counts["hold"],
                "sell_n": counts["strongSell"] + counts["sell"],
                "n_analysts": total,
                "consensus_status": "OK",
                "consensus_source": "Finnhub",
            }

            if delay_seconds:
                time.sleep(delay_seconds)
            try:
                target = _get_json(session, "/stock/price-target", {"symbol": finnhub_symbol, "token": api_key}, max_retries=1)
                if isinstance(target, dict) and target.get("targetMean"):
                    fields["target_price"] = target["targetMean"]
            except Exception:
                pass

            for field, value in fields.items():
                if value is None:
                    continue
                observations.append({
                    "ticker": yahoo_ticker,
                    "isin": isin,
                    "finnhub_symbol": finnhub_symbol,
                    "field": field,
                    "value": value,
                    "source": "Finnhub",
                })
        except Exception as exc:
            failures.append({"ticker": yahoo_ticker, "isin": isin, "finnhub_symbol": finnhub_symbol, "reason": type(exc).__name__})

        if delay_seconds:
            time.sleep(delay_seconds)

    _save_symbol_cache(symbol_cache_path, cache)
    return observations, failures
