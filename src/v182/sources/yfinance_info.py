from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from v182.sources.rate_limit import StartRateLimiter

# Raw yfinance fields are kept under stable V18.2 names. The Committee Master
# resolves them to canonical V21 criteria through explicit semantic aliases.
# ETF-specific values are kept raw here and converted only in the ETF wave when
# their units/currency are explicit enough to do so without guessing.
FIELDS = {
    "marketCap":"market_cap",
    "trailingPE":"per_ttm_yf",
    "forwardPE":"per_forward_yf",
    "priceToBook":"pb",
    "returnOnEquity":"roe_api",
    "returnOnAssets":"roa",
    "debtToEquity":"debt_to_equity",
    "totalDebt":"total_debt_yf",
    "ebitda":"ebitda_yf",
    "freeCashflow":"free_cash_flow",
    "operatingMargins":"marge_ebit",
    "profitMargins":"marge_nette",
    "revenueGrowth":"revenue_growth_yf",
    "earningsGrowth":"earnings_growth_yf",
    "targetMeanPrice":"target_mean_yf",
    "targetHighPrice":"target_high_yf",
    "targetLowPrice":"target_low_yf",
    "currentPrice":"current_price_yf",
    "numberOfAnalystOpinions":"n_analysts_yf",
    "recommendationMean":"recommendation_mean_yf",
    "recommendationKey":"recommendation_key_yf",
    "beta":"beta",
    "dividendYield":"dividend_yield_pct",
    "dividendRate":"dividend_rate_yf",
    "payoutRatio":"payout_ratio",
    "sector":"sector_yf",
    "industry":"industry_yf",
    "country":"country_yf",
    "exchange":"exchange_yf",
    "fullExchangeName":"full_exchange_name_yf",
    "currency":"currency_yf",
    "longName":"long_name_yf",
    "quoteType":"quote_type_yf",
    "annualReportExpenseRatio":"annual_report_expense_ratio_yf",
    "totalAssets":"total_assets_yf",
    "fundFamily":"fund_family_yf",
    "category":"category_yf",
    "legalType":"legal_type_yf",
    "beta3Year":"beta3y_yf",
    "yield":"yield_yf",
    "earningsTimestamp":"earnings_timestamp_yf",
    "earningsTimestampStart":"earnings_timestamp_start_yf",
    "earningsTimestampEnd":"earnings_timestamp_end_yf",
}


def _future_earnings_fields(info: dict, now_ts: float | None = None) -> dict:
    """Expose objective earnings-calendar fields without inventing catalyst score."""
    now=now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    candidates=[]
    for key in ("earningsTimestampStart","earningsTimestamp","earningsTimestampEnd"):
        value=info.get(key)
        try:
            ts=float(value)
        except (TypeError,ValueError):
            continue
        if ts >= now - 86400:  # tolerate same-day/time-zone publication windows
            candidates.append(ts)
    if not candidates:
        return {}
    next_ts=min(candidates)
    days=(next_ts-now)/86400.0
    return {
        "days_to_earnings":round(days,3),
        "earnings_within_7d_flag":1.0 if 0 <= days <= 7 else 0.0,
        "earnings_within_30d_flag":1.0 if 0 <= days <= 30 else 0.0,
        "next_earnings_timestamp_yf":int(next_ts),
    }


def _collect_one(ticker: str, yf, limiter: StartRateLimiter) -> tuple[list[dict], dict | None]:
    limiter.wait()
    try:
        info = yf.Ticker(ticker).get_info()
        observations=[]
        for source_field, target_field in FIELDS.items():
            value = info.get(source_field)
            if value is not None:
                observations.append({"ticker":ticker,"field":target_field,"value":value,"source":"yfinance"})
        for field,value in _future_earnings_fields(info).items():
            observations.append({"ticker":ticker,"field":field,"value":value,"source":"yfinance"})
        return observations, None
    except Exception as exc:
        return [], {"ticker": ticker, "error": type(exc).__name__, "detail": str(exc)[:160]}


def collect_info(
    tickers: list[str], delay_seconds: float = 0.4, max_workers: int = 4,
) -> tuple[list[dict], list[dict]]:
    """Collect yfinance metadata with bounded concurrency and stable rate cadence.

    `delay_seconds` is now the minimum interval between request starts globally,
    rather than dead time after every completed request. Network waits may overlap
    across a small worker pool, but the source request-start cadence is not raised.
    """
    import yfinance as yf

    unique=sorted({x for x in tickers if x})
    if not unique:
        return [], []
    limiter=StartRateLimiter(delay_seconds)
    observations: list[dict]=[]
    failures: list[dict]=[]
    workers=max(1,min(int(max_workers),len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures=[executor.submit(_collect_one,ticker,yf,limiter) for ticker in unique]
        for future in as_completed(futures):
            obs,failure=future.result()
            observations.extend(obs)
            if failure is not None:
                failures.append(failure)
    observations.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("field",""))))
    failures.sort(key=lambda row:str(row.get("ticker","")))
    return observations, failures
