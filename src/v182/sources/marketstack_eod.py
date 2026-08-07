from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

MARKETSTACK_BASE = "https://api.marketstack.com/v2"


@dataclass(frozen=True)
class MarketstackResult:
    frames: dict[str, object]
    failures: list[dict]
    attempted: int
    successful: int


def _pick_rows(data: list[dict], symbol: str, expected_mic: str | None) -> list[dict]:
    symbol_upper = symbol.upper()
    rows = [r for r in data if str(r.get("symbol") or "").upper() == symbol_upper]
    if not rows:
        rows = list(data)
    if expected_mic:
        exact = [r for r in rows if str(r.get("exchange") or "").upper() == expected_mic.upper()]
        if exact:
            return exact
        exchanges = {str(r.get("exchange") or "").upper() for r in rows if r.get("exchange")}
        if len(exchanges) > 1:
            return []
    return rows


def fetch_eod_history(
    requests_spec: list[dict],
    api_key: str,
    history_days: int = 365,
    max_symbols: int = 4,
    auto_adjust: bool = True,
    min_rows: int = 60,
    delay_seconds: float = 0.25,
    timeout: int = 30,
) -> MarketstackResult:
    """Fetch up to ``max_symbols`` fallback histories from Marketstack v2.

    Each symbol consumes quota according to Marketstack's billing model, so the
    caller deliberately supplies a conservative per-run cap. The returned
    frames use the canonical Yahoo ticker as key so the existing indicator
    engine can consume them without changing ISIN mappings.
    """
    import pandas as pd
    import requests

    if not api_key:
        return MarketstackResult({}, [{"reason": "MISSING_API_KEY"}], 0, 0)

    unique = []
    seen = set()
    for spec in requests_spec:
        canonical = str(spec.get("canonical_ticker") or "").strip()
        symbol = str(spec.get("symbol") or "").strip()
        if not canonical or not symbol or canonical in seen:
            continue
        seen.add(canonical)
        unique.append(spec)
        if len(unique) >= max(0, int(max_symbols)):
            break

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=max(30, int(history_days)))
    frames: dict[str, pd.DataFrame] = {}
    failures: list[dict] = []

    for spec in unique:
        canonical = str(spec["canonical_ticker"]).strip()
        symbol = str(spec["symbol"]).strip()
        expected_mic = str(spec.get("expected_mic") or "").strip()
        params = {
            "access_key": api_key,
            "symbols": symbol,
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
            "limit": 1000,
        }
        try:
            resp = requests.get(f"{MARKETSTACK_BASE}/eod", params=params, timeout=timeout)
            resp.raise_for_status()
            body = resp.json()
            if isinstance(body, dict) and body.get("error"):
                failures.append({"ticker": canonical, "symbol": symbol, "reason": "API_ERROR", "detail": str(body.get("error"))[:300]})
                time.sleep(delay_seconds)
                continue
            data = body.get("data", []) if isinstance(body, dict) else []
            rows = _pick_rows(data, symbol, expected_mic)
            if not rows:
                failures.append({"ticker": canonical, "symbol": symbol, "reason": "NO_MATCHING_EXCHANGE", "expected_mic": expected_mic})
                time.sleep(delay_seconds)
                continue

            records = []
            for row in rows:
                date = row.get("date")
                if not date:
                    continue
                def chosen(adj_key: str, raw_key: str):
                    if auto_adjust and row.get(adj_key) is not None:
                        return row.get(adj_key)
                    return row.get(raw_key)
                records.append({
                    "Date": pd.to_datetime(date, utc=True),
                    "Open": chosen("adj_open", "open"),
                    "High": chosen("adj_high", "high"),
                    "Low": chosen("adj_low", "low"),
                    "Close": chosen("adj_close", "close"),
                    "Volume": chosen("adj_volume", "volume"),
                })
            if not records:
                failures.append({"ticker": canonical, "symbol": symbol, "reason": "EMPTY_DATA"})
                time.sleep(delay_seconds)
                continue

            frame = pd.DataFrame(records).drop_duplicates("Date").sort_values("Date").set_index("Date")
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
            frame = frame.dropna(subset=["Close"])
            if len(frame) < int(min_rows):
                failures.append({"ticker": canonical, "symbol": symbol, "reason": "INSUFFICIENT_ROWS", "rows": len(frame)})
                time.sleep(delay_seconds)
                continue
            frames[canonical] = frame
        except Exception as exc:
            failures.append({"ticker": canonical, "symbol": symbol, "reason": type(exc).__name__})
        time.sleep(delay_seconds)

    return MarketstackResult(frames, failures, len(unique), len(frames))


def save_marketstack_cache(frames: dict[str, object], cache_dir: str | Path, prefix: str = "history_marketstack") -> str | None:
    """Persist Marketstack frames using the same MultiIndex layout as yfinance."""
    import pandas as pd

    if not frames:
        return None
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    combined = pd.concat({ticker: frame for ticker, frame in sorted(frames.items())}, axis=1)
    output = cache / f"{prefix}_00000.parquet"
    combined.to_parquet(output)
    return str(output)
