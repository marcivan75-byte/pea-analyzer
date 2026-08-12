from __future__ import annotations
from typing import Any
import time

# Raw yfinance fields are kept under stable V18.2 names. The Committee Master
# resolves them to canonical V21 criteria through explicit semantic aliases.
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
    "payoutRatio":"payout_ratio",
}

def collect_info(tickers: list[str], delay_seconds: float = 0.4) -> tuple[list[dict], list[dict]]:
    import yfinance as yf  # import différé : yfinance n'est requis qu'au moment de l'appel réseau
    observations, failures = [], []
    for ticker in sorted({x for x in tickers if x}):
        try:
            info = yf.Ticker(ticker).get_info()
            for source_field, target_field in FIELDS.items():
                value = info.get(source_field)
                if value is not None:
                    observations.append({
                        "ticker": ticker,
                        "field": target_field,
                        "value": value,
                        "source": "yfinance",
                    })
        except Exception as exc:
            failures.append({"ticker": ticker, "error": type(exc).__name__})
        time.sleep(delay_seconds)
    return observations, failures
