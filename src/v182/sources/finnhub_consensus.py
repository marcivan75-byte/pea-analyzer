from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import csv
import time

FINNHUB_BASE = "https://finnhub.io/api/v1"
_SCORE_WEIGHTS = {"strongBuy": 5, "buy": 4, "hold": 3, "sell": 2, "strongSell": 1}


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


def _pick_lookup_result(results: list[dict], yahoo_ticker: str | None) -> dict | None:
    if not results:
        return None
    wanted = str(yahoo_ticker or "").upper().strip()
    wanted_base = _ticker_base(wanted)

    def score(row: dict) -> tuple[int, str]:
        symbol = str(row.get("symbol") or "").upper().strip()
        display = str(row.get("displaySymbol") or "").upper().strip()
        kind = str(row.get("type") or "").lower()
        s = 0
        if symbol == wanted or display == wanted:
            s += 20
        if _ticker_base(symbol) == wanted_base or _ticker_base(display) == wanted_base:
            s += 8
        if wanted and "." in wanted and (symbol.endswith(wanted[wanted.index("."):]) or display.endswith(wanted[wanted.index("."):])):
            s += 4
        if "stock" in kind or "common" in kind or "equity" in kind:
            s += 2
        return (s, symbol)

    return max(results, key=score)


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


def _get_json(session, path: str, params: dict, max_retries: int = 2, backoff_seconds: float = 2.0):
    for attempt in range(max_retries + 1):
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
) -> tuple[list[dict], list[dict]]:
    """Fetch Finnhub analyst consensus with ISIN-based symbol resolution.

    Finnhub documents symbol lookup by ISIN and uses its own exchange-aware
    symbology. Yahoo symbols are therefore no longer assumed to be valid
    Finnhub symbols. Resolutions are cached so later scheduled runs do not
    repeat the lookup unnecessarily.
    """
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
        finnhub_symbol = str(cached.get("finnhub_symbol") or "").strip()

        try:
            if not finnhub_symbol:
                query = isin or yahoo_ticker or security["name"]
                lookup = _get_json(session, "/search", {"q": query, "token": api_key}, max_retries=max_retries)
                best = _pick_lookup_result((lookup or {}).get("result", []), yahoo_ticker)
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
                    failures.append({"ticker": yahoo_ticker, "isin": isin, "reason": "SYMBOL_UNRESOLVED"})
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

            score = round(sum(counts[k] * w for k, w in _SCORE_WEIGHTS.items()) / total, 2)
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
