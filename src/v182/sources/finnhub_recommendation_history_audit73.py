"""Audit 73 sidecar for lossless Finnhub monthly recommendation history.

Finnhub's ``stock/recommendation`` endpoint returns a monthly series.  The legacy
collector reduces that series to the latest row plus one comparison row.  This
sidecar preserves every returned monthly row while keeping two clocks separate:

* ``provider_period``: the month/date Finnhub attaches to the recommendation row;
* ``available_at``: when this system actually retrieved the payload.

A provider period is never promoted to a PIT knowledge timestamp.  Historical
rows first retrieved today are therefore not usable as if they had been known in
the past.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Callable

from v182.sources.finnhub_consensus import FINNHUB_BASE, _SCORE_WEIGHTS, _counts, _label_from_score, _score_from_counts
from v182.sources.rate_limit import StartRateLimiter

VERSION = "AUDIT73_FINNHUB_RECOMMENDATION_HISTORY_V1"


def _utc_iso(value: datetime | None = None) -> str:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def normalize_recommendation_series(payload: object, *, captured_at: datetime | None = None) -> list[dict]:
    """Normalize every usable Finnhub recommendation row without backdating it."""
    if not isinstance(payload, list):
        return []
    available_at = _utc_iso(captured_at)
    rows: list[dict] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        counts = _counts(raw)
        score = _score_from_counts(counts)
        if score is None:
            continue
        total = sum(counts.values())
        buy_n = counts["strongBuy"] + counts["buy"]
        hold_n = counts["hold"]
        sell_n = counts["sell"] + counts["strongSell"]
        rows.append({
            "provider_period": str(raw.get("period") or "") or None,
            "available_at": available_at,
            "consensus": _label_from_score(float(score)),
            "consensus_score": round(float(score), 4),
            "n_analysts": int(total),
            "buy_n": int(buy_n),
            "hold_n": int(hold_n),
            "sell_n": int(sell_n),
            "strong_buy_n": int(counts["strongBuy"]),
            "buy_plain_n": int(counts["buy"]),
            "hold_plain_n": int(counts["hold"]),
            "sell_plain_n": int(counts["sell"]),
            "strong_sell_n": int(counts["strongSell"]),
            "provider": "Finnhub",
            "provider_period_is_knowledge_timestamp": False,
            "artificial_available_at_assigned": False,
        })
    rows.sort(key=lambda row: str(row.get("provider_period") or ""), reverse=True)
    return rows


def _load(path: Path) -> dict:
    if not path.exists():
        return {"version": VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": VERSION, "entries": {}}
    if payload.get("version") != VERSION or not isinstance(payload.get("entries"), dict):
        return {"version": VERSION, "entries": {}}
    return payload


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def append_capture(path: str | Path, *, ticker: str, rows: list[dict], captured_at: datetime, payload_sha256: str) -> dict:
    """Append one real retrieval event. Re-reading a cache cannot call this function."""
    target = Path(path)
    payload = _load(target)
    entries: dict[str, list[dict]] = payload["entries"]
    symbol = str(ticker or "").strip().upper()
    if not symbol or not rows:
        return {"status": "NO_VALID_ROWS", "captures_appended": 0, "rows_appended": 0}
    captured = _utc_iso(captured_at)
    history = list(entries.get(symbol) or [])
    key = (captured, str(payload_sha256 or ""))
    seen = {(str(item.get("captured_at_utc") or ""), str(item.get("payload_sha256") or "")) for item in history if isinstance(item, dict)}
    if key in seen:
        return {"status": "DUPLICATE_CAPTURE", "captures_appended": 0, "rows_appended": 0}
    history.append({
        "captured_at_utc": captured,
        "payload_sha256": str(payload_sha256 or ""),
        "rows": rows,
    })
    history.sort(key=lambda item: (str(item.get("captured_at_utc") or ""), str(item.get("payload_sha256") or "")))
    entries[symbol] = history
    payload["updated_at_utc"] = captured
    payload["policy"] = {
        "append_only_capture_history": True,
        "provider_period_preserved": True,
        "provider_period_used_as_available_at": False,
        "future_fill_forbidden": True,
        "production_scoring_influence": 0.0,
    }
    _save(target, payload)
    return {"status": "APPENDED", "captures_appended": 1, "rows_appended": len(rows)}


def collect_recommendation_history_cached(
    tickers: list[str],
    api_key: str,
    history_path: str | Path,
    *,
    delay_seconds: float = 1.1,
    timeout_seconds: float = 15.0,
    fetcher: Callable[..., object] | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Collect the full monthly series for a bounded set of tickers.

    This sidecar is deliberately separate from the production Finnhub current-state
    cache. It must be called only when a real HTTP retrieval is desired; there is
    no cache-hit path that can manufacture a new PIT capture.
    """
    if fetcher is None:
        import requests
        fetcher = requests.get
    limiter = StartRateLimiter(delay_seconds)
    observations: list[dict] = []
    failures: list[dict] = []
    captures = 0
    preserved_rows = 0
    unique = sorted({str(t).strip().upper() for t in tickers if str(t).strip()})
    for ticker in unique:
        try:
            limiter.wait()
            response = fetcher(
                f"{FINNHUB_BASE}/stock/recommendation",
                params={"symbol": ticker, "token": api_key},
                timeout=timeout_seconds,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            raw_payload = response.json()
            # Timestamp only after a successful response + JSON decode.
            captured_at = datetime.now(timezone.utc)
            serialized = json.dumps(raw_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            rows = normalize_recommendation_series(raw_payload, captured_at=captured_at)
            if not rows:
                failures.append({"ticker": ticker, "reason": "NO_USABLE_RECOMMENDATION_HISTORY"})
                continue
            result = append_capture(
                history_path,
                ticker=ticker,
                rows=rows,
                captured_at=captured_at,
                payload_sha256=sha256(serialized.encode("utf-8", errors="replace")).hexdigest(),
            )
            captures += int(result["captures_appended"])
            preserved_rows += int(result["rows_appended"])
            observations.extend({"ticker": ticker, **row} for row in rows)
        except Exception as exc:
            failures.append({"ticker": ticker, "reason": type(exc).__name__, "detail": str(exc)[:160]})
    return observations, failures, {
        "version": VERSION,
        "requested": len(unique),
        "captures_appended": captures,
        "rows_appended": preserved_rows,
        "failures": len(failures),
        "provider_period_used_as_available_at": False,
        "production_scoring_influence": 0.0,
    }


def load_strict_pit_observations(path: str | Path) -> list[dict]:
    """Flatten captures using retrieval time as the only PIT availability clock."""
    payload = _load(Path(path))
    output: list[dict] = []
    for ticker, captures in payload["entries"].items():
        if not isinstance(captures, list):
            continue
        for capture in captures:
            if not isinstance(capture, dict):
                continue
            captured_at = str(capture.get("captured_at_utc") or "")
            for row in capture.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                output.append({
                    "ticker": ticker,
                    "provider_period": row.get("provider_period"),
                    "available_at": captured_at,
                    "consensus": row.get("consensus"),
                    "consensus_score": row.get("consensus_score"),
                    "n_analysts": row.get("n_analysts"),
                    "buy_n": row.get("buy_n"),
                    "hold_n": row.get("hold_n"),
                    "sell_n": row.get("sell_n"),
                    "provider": "Finnhub",
                    "provider_period_is_knowledge_timestamp": False,
                    "capture_sha256": capture.get("payload_sha256"),
                })
    output.sort(key=lambda row: (str(row.get("available_at") or ""), str(row.get("ticker") or ""), str(row.get("provider_period") or "")))
    return output
