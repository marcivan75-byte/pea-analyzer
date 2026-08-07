from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
import re
import time

MARKETSTACK_TICKERS_LIST_URL = "https://api.marketstack.com/v2/tickerslist"
CACHE_COLUMNS = [
    "universe", "isin", "name", "original_yahoo_ticker", "expected_mic",
    "marketstack_symbol", "matched_name", "confidence", "status", "updated_at",
]


@dataclass(frozen=True)
class SymbolResolutionResult:
    resolved: dict[str, str]
    failures: list[dict]
    api_attempted: int
    api_successful: int
    cache_hits: int


def _norm(text: str | None) -> str:
    text = str(text or "").upper()
    text = re.sub(r"\b(SA|SE|NV|N V|SPA|S P A|PLC|AG|SCA|SAS|GROUP|GROUPE|HOLDING|HOLDINGS)\b", " ", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def _name_score(expected: str, candidate: str) -> float:
    a, b = _norm(expected), _norm(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return min(0.98, 0.82 + 0.16 * min(len(a), len(b)) / max(len(a), len(b)))
    return SequenceMatcher(None, a, b).ratio()


def _candidate_rows(body: dict, expected_mic: str) -> list[dict]:
    rows = body.get("data", []) if isinstance(body, dict) else []
    if not isinstance(rows, list):
        return []
    mic = str(expected_mic or "").upper()
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_mic = str((row.get("stock_exchange") or {}).get("mic") or "").upper()
        ticker = str(row.get("ticker") or row.get("symbol") or "").strip()
        if ticker and (not mic or row_mic == mic):
            result.append(row)
    return result


def resolve_one(
    name: str,
    original_yahoo_ticker: str,
    expected_mic: str,
    api_key: str,
    min_confidence: float = 0.72,
    timeout: int = 30,
) -> tuple[dict | None, dict | None]:
    """Resolve one Marketstack-specific ticker by company name + exact MIC."""
    import requests

    if not api_key:
        return None, {"reason": "MISSING_API_KEY"}
    query = str(name or "").strip()
    if not query:
        query = str(original_yahoo_ticker or "").split(".", 1)[0].strip()
    if not query or not expected_mic:
        return None, {"reason": "INSUFFICIENT_IDENTITY", "query": query, "expected_mic": expected_mic}

    try:
        response = requests.get(
            MARKETSTACK_TICKERS_LIST_URL,
            params={"access_key": api_key, "search": query, "exchange": expected_mic, "limit": 20},
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and body.get("error"):
            return None, {"reason": "API_ERROR", "detail": str(body.get("error"))[:300]}
        rows = _candidate_rows(body, expected_mic)
        if not rows:
            return None, {"reason": "NO_MATCH", "query": query, "expected_mic": expected_mic}

        scored = []
        for row in rows:
            candidate_name = str(row.get("name") or "")
            score = _name_score(name, candidate_name)
            ticker = str(row.get("ticker") or row.get("symbol") or "").strip()
            if ticker.upper() == str(original_yahoo_ticker or "").upper():
                score = max(score, 0.95)
            scored.append((score, ticker, candidate_name, row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best = scored[0]
        if best[0] < float(min_confidence):
            return None, {
                "reason": "LOW_CONFIDENCE", "query": query, "expected_mic": expected_mic,
                "best_symbol": best[1], "best_name": best[2], "confidence": round(best[0], 4),
            }
        if len(scored) > 1 and best[0] - scored[1][0] < 0.03 and best[1] != scored[1][1]:
            return None, {
                "reason": "AMBIGUOUS", "query": query, "expected_mic": expected_mic,
                "best_symbol": best[1], "second_symbol": scored[1][1],
                "best_confidence": round(best[0], 4), "second_confidence": round(scored[1][0], 4),
            }
        return {
            "symbol": best[1], "matched_name": best[2], "confidence": round(best[0], 4),
            "expected_mic": expected_mic,
        }, None
    except Exception as exc:
        return None, {"reason": type(exc).__name__}


def _fresh(row, resolved_ttl_days: int, negative_ttl_days: int) -> bool:
    status = str(row.get("status") or "").upper()
    ttl = resolved_ttl_days if status == "RESOLVED" else negative_ttl_days
    try:
        stamp = datetime.fromisoformat(str(row.get("updated_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp >= datetime.now(timezone.utc) - timedelta(days=max(1, int(ttl)))
    except Exception:
        return False


def resolve_marketstack_symbols(
    rows,
    universe: str,
    cache_path: str | Path,
    api_key: str,
    max_new_resolutions: int = 1,
    min_confidence: float = 0.72,
    resolved_ttl_days: int = 90,
    negative_ttl_days: int = 30,
    delay_seconds: float = 0.25,
) -> SymbolResolutionResult:
    """Resolve/cache Marketstack symbols for prioritized failed Yahoo rows."""
    import pandas as pd
    from v182.mapping.etf_isin_resolver import expected_mic

    path = Path(cache_path)
    if path.exists():
        cache = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
        for col in CACHE_COLUMNS:
            if col not in cache.columns:
                cache[col] = ""
        cache = cache[CACHE_COLUMNS]
    else:
        cache = pd.DataFrame(columns=CACHE_COLUMNS)

    now = datetime.now(timezone.utc).isoformat()
    by_key = {(r["universe"], r["isin"]): r for _, r in cache.iterrows()}
    resolved: dict[str, str] = {}
    failures: list[dict] = []
    api_attempted = 0
    api_successful = 0
    cache_hits = 0
    replacements: list[dict] = []

    for _, row in rows.iterrows():
        original = str(row.get("yahoo_ticker") or "").strip()
        isin = str(row.get("isin") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        mic = expected_mic(original, isin)
        if not original or not isin or not mic:
            failures.append({"ticker": original, "isin": isin, "reason": "INSUFFICIENT_IDENTITY"})
            continue

        old = by_key.get((universe, isin))
        old_same_mic = old is not None and str(old.get("expected_mic") or "").upper() == mic
        if old_same_mic and _fresh(old, resolved_ttl_days, negative_ttl_days):
            if str(old.get("status") or "").upper() == "RESOLVED":
                symbol = str(old.get("marketstack_symbol") or "").strip()
                if symbol:
                    resolved[original] = symbol
                    cache_hits += 1
            else:
                failures.append({"ticker": original, "isin": isin, "reason": str(old.get("status") or "CACHED_NEGATIVE")})
            continue

        if api_attempted >= max(0, int(max_new_resolutions)):
            failures.append({"ticker": original, "isin": isin, "reason": "RESOLUTION_BUDGET_DEFERRED"})
            continue

        api_attempted += 1
        match, failure = resolve_one(name, original, mic, api_key, min_confidence=min_confidence)
        if match:
            api_successful += 1
            resolved[original] = match["symbol"]
            replacements.append({
                "universe": universe, "isin": isin, "name": name,
                "original_yahoo_ticker": original, "expected_mic": mic,
                "marketstack_symbol": match["symbol"], "matched_name": match["matched_name"],
                "confidence": str(match["confidence"]), "status": "RESOLVED", "updated_at": now,
            })
        else:
            failure = {"ticker": original, "isin": isin, **(failure or {"reason": "UNKNOWN"})}
            failures.append(failure)
            reason = str(failure.get("reason") or "")
            if reason in {"NO_MATCH", "LOW_CONFIDENCE", "AMBIGUOUS", "INSUFFICIENT_IDENTITY"}:
                replacements.append({
                    "universe": universe, "isin": isin, "name": name,
                    "original_yahoo_ticker": original, "expected_mic": mic,
                    "marketstack_symbol": "", "matched_name": str(failure.get("best_name") or ""),
                    "confidence": str(failure.get("confidence") or failure.get("best_confidence") or ""),
                    "status": reason, "updated_at": now,
                })
        time.sleep(max(0.0, float(delay_seconds)))

    replace_keys = {(r["universe"], r["isin"]) for r in replacements}
    if not cache.empty and replace_keys:
        cache = cache[[ (u, i) not in replace_keys for u, i in zip(cache["universe"], cache["isin"]) ]]
    if replacements:
        cache = pd.concat([cache, pd.DataFrame(replacements, columns=CACHE_COLUMNS)], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    cache.to_csv(path, sep=";", index=False, encoding="utf-8-sig")

    return SymbolResolutionResult(resolved, failures, api_attempted, api_successful, cache_hits)
