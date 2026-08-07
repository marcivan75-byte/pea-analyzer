from __future__ import annotations
from typing import Any
import time

FIELDS = {
    "marketCap": "market_cap",
    "trailingPE": "per_ttm_yf",
    "forwardPE": "per_forward_yf",
    "priceToBook": "pb",
    "returnOnEquity": "roe_api",
    "returnOnAssets": "roa",
    "debtToEquity": "debt_to_equity",
    "freeCashflow": "free_cash_flow",
    "operatingMargins": "marge_ebit",
    "profitMargins": "marge_nette",
    "targetMeanPrice": "target_mean_yf",
    "targetHighPrice": "target_high_yf",
    "targetLowPrice": "target_low_yf",
    "numberOfAnalystOpinions": "n_analysts_yf",
    "recommendationMean": "recommendation_mean_yf",
    "recommendationKey": "recommendation_key_yf",
    "beta": "beta",
    "dividendYield": "dividend_yield_pct",
}


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "ratelimit" in text or "rate limit" in text or "too many requests" in text or "429" in text


def _fast_info_fallback(ticker_obj, ticker: str) -> list[dict]:
    """Best-effort fallback using yfinance.fast_info.

    It intentionally returns only fields that fast_info can provide without
    pretending to have the richer quoteSummary fundamentals.
    """
    out: list[dict] = []
    try:
        fast = ticker_obj.fast_info
        market_cap = fast.get("market_cap") if hasattr(fast, "get") else None
        if market_cap is not None:
            out.append({"ticker": ticker, "field": "market_cap", "value": market_cap, "source": "yfinance_fast_info"})
    except Exception:
        pass
    return out


def collect_info(
    tickers: list[str],
    delay_seconds: float = 0.8,
    max_retries: int = 2,
    rate_limit_backoff_seconds: float = 20.0,
    max_consecutive_rate_limits: int = 3,
) -> tuple[list[dict], list[dict]]:
    """Collect yfinance metadata with bounded retries and a rate-limit circuit breaker.

    The old implementation retried hundreds of symbols immediately after a
    large OHLCV bulk download. When Yahoo throttled the runner this produced
    hundreds of identical failures. This implementation backs off and stops
    the metadata wave once throttling is clearly persistent, preserving the
    rest of the GitHub Actions runtime for fallbacks and reporting.
    """
    import yfinance as yf

    unique = sorted({x for x in tickers if x})
    observations: list[dict] = []
    failures: list[dict] = []
    consecutive_rate_limits = 0

    for index, ticker in enumerate(unique):
        ticker_obj = yf.Ticker(ticker)
        success = False
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                info = ticker_obj.get_info() or {}
                for source_field, target_field in FIELDS.items():
                    value = info.get(source_field)
                    if value is not None:
                        observations.append({
                            "ticker": ticker,
                            "field": target_field,
                            "value": value,
                            "source": "yfinance",
                        })
                success = True
                consecutive_rate_limits = 0
                break
            except Exception as exc:  # yfinance raises several backend-specific exception types
                last_exc = exc
                if _is_rate_limit(exc):
                    consecutive_rate_limits += 1
                    if consecutive_rate_limits >= max_consecutive_rate_limits:
                        break
                    if attempt < max_retries:
                        time.sleep(rate_limit_backoff_seconds * (2 ** attempt))
                        continue
                break

        if not success:
            fallback = _fast_info_fallback(ticker_obj, ticker)
            observations.extend(fallback)
            reason = "RATE_LIMIT" if last_exc and _is_rate_limit(last_exc) else (type(last_exc).__name__ if last_exc else "NO_DATA")
            failures.append({"ticker": ticker, "reason": reason, "fallback_fields": len(fallback)})

        if consecutive_rate_limits >= max_consecutive_rate_limits:
            for remaining in unique[index + 1:]:
                failures.append({"ticker": remaining, "reason": "RATE_LIMIT_CIRCUIT_OPEN", "fallback_fields": 0})
            break

        if delay_seconds:
            time.sleep(delay_seconds)

    return observations, failures
