from __future__ import annotations

from datetime import datetime, timezone
import time
import pandas as pd


def collect_snapshots(
    securities: list[dict],
    delay_seconds: float = 0.45,
    max_consecutive_rate_limits: int = 3,
) -> tuple[pd.DataFrame, list[dict]]:
    """Best-effort ETF AUM/NAV snapshot collector from yfinance metadata.

    This is evidence C and is used only to build persistent history. A snapshot
    is accepted only when both totalAssets and navPrice are positive. Market
    price is never substituted for NAV.
    """
    import yfinance as yf

    rows: list[dict] = []
    failures: list[dict] = []
    consecutive_rate_limits = 0
    today = datetime.now(timezone.utc).date().isoformat()

    for index, security in enumerate(securities):
        isin = str(security.get("isin") or "").strip().upper()
        ticker = str(security.get("yahoo_ticker") or "").strip()
        provider = str(security.get("provider") or "")
        if not isin or not ticker:
            failures.append({"isin": isin, "ticker": ticker, "reason": "MISSING_IDENTITY"})
            continue
        try:
            info = yf.Ticker(ticker).get_info() or {}
            aum = _positive(info.get("totalAssets"))
            nav = _positive(info.get("navPrice"))
            if aum is None or nav is None:
                failures.append({"isin": isin, "ticker": ticker, "reason": "MISSING_AUM_OR_NAV"})
            else:
                rows.append({
                    "date": today,
                    "isin": isin,
                    "aum": aum,
                    "nav": nav,
                    "source": "YFINANCE_ETF_INFO",
                    "evidence_level": "C",
                    "provider": provider,
                })
            consecutive_rate_limits = 0
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}".lower()
            rate_limited = any(token in text for token in ("429", "rate limit", "ratelimit", "too many requests"))
            consecutive_rate_limits = consecutive_rate_limits + 1 if rate_limited else 0
            failures.append({"isin": isin, "ticker": ticker, "reason": "RATE_LIMIT" if rate_limited else type(exc).__name__})
            if consecutive_rate_limits >= max_consecutive_rate_limits:
                for remaining in securities[index + 1:]:
                    failures.append({
                        "isin": remaining.get("isin"),
                        "ticker": remaining.get("yahoo_ticker"),
                        "reason": "RATE_LIMIT_CIRCUIT_OPEN",
                    })
                break
        if delay_seconds:
            time.sleep(max(0.0, float(delay_seconds)))

    return pd.DataFrame(rows, columns=["date", "isin", "aum", "nav", "source", "evidence_level", "provider"]), failures


def _positive(value) -> float | None:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None
