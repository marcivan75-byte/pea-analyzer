from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import pandas as pd

from v182.sources.rate_limit import StartRateLimiter


def _normalise_weight(value) -> float | None:
    try:
        x=float(value)
    except (TypeError,ValueError):
        return None
    if not math.isfinite(x) or x < 0:
        return None
    return x/100.0 if x > 1.0 else x


def top_holdings_concentration_pct(top_holdings: pd.DataFrame | None) -> float | None:
    if top_holdings is None or not isinstance(top_holdings,pd.DataFrame) or top_holdings.empty:
        return None
    candidates=("Holding Percent","% Assets","Percent Assets","holdingPercent","weight","Weight")
    col=next((c for c in candidates if c in top_holdings.columns),None)
    if col is None:
        numeric=[c for c in top_holdings.columns if pd.api.types.is_numeric_dtype(top_holdings[c])]
        col=numeric[-1] if numeric else None
    if col is None:
        return None
    weights=[w for w in (_normalise_weight(v) for v in top_holdings[col].tolist()) if w is not None]
    if not weights:
        return None
    return round(min(1.0,sum(weights))*100.0,6)


def sector_hhi(sector_weightings) -> float | None:
    if not isinstance(sector_weightings,dict) or not sector_weightings:
        return None
    weights=[w for w in (_normalise_weight(v) for v in sector_weightings.values()) if w is not None]
    if not weights:
        return None
    total=sum(weights)
    if total <= 0:
        return None
    norm=[w/total for w in weights]
    return round(sum(w*w for w in norm),8)


def diversification_score(sector_hhi_value: float | None) -> float | None:
    """Canonical direct diversification score retained by the ETF process."""
    if sector_hhi_value is None:
        return None
    return round(max(0.0,min(100.0,(1.0-float(sector_hhi_value))*100.0)),6)


def _collect_one(ticker: str, yf, limiter: StartRateLimiter) -> tuple[list[dict], dict | None]:
    limiter.wait()
    try:
        funds=yf.Ticker(ticker).funds_data
        top=getattr(funds,"top_holdings",None)
        sectors=getattr(funds,"sector_weightings",None)
        concentration=top_holdings_concentration_pct(top)
        hhi=sector_hhi(sectors)
        score=diversification_score(hhi)
        observations=[]
        if concentration is not None:
            observations.extend([
                {"ticker":ticker,"field":"direct_top_holdings_concentration_pct","value":concentration,"source":"yfinance.funds_data"},
                {"ticker":ticker,"field":"top_holdings_concentration_pct","value":concentration,"source":"yfinance.funds_data"},
            ])
        if hhi is not None:
            observations.append({"ticker":ticker,"field":"direct_sector_hhi","value":hhi,"source":"yfinance.funds_data"})
        if score is not None:
            observations.extend([
                {"ticker":ticker,"field":"direct_diversification_score","value":score,"source":"yfinance.funds_data"},
                {"ticker":ticker,"field":"diversification_direct_score","value":score,"source":"yfinance.funds_data"},
            ])
        if isinstance(top,pd.DataFrame) and not top.empty:
            observations.append({"ticker":ticker,"field":"top_holdings_observed_count","value":int(len(top)),"source":"yfinance.funds_data"})
        failure=None
        if concentration is None and hhi is None:
            failure={"ticker":ticker,"reason":"NO_FUND_STRUCTURE_DATA"}
        return observations,failure
    except Exception as exc:
        return [],{"ticker":ticker,"reason":type(exc).__name__,"detail":str(exc)[:180]}


def collect_fund_structure(
    tickers: list[str],
    delay_seconds: float = 0.2,
    max_workers: int = 4,
) -> tuple[list[dict], list[dict]]:
    """Collect the same ETF structure universe with bounded concurrent I/O.

    Request starts are globally rate-limited by ``delay_seconds``. Network waits
    may overlap across a small worker pool, but no ticker, field, missing-value
    rule, evidence rule or scoring formula is changed.
    """
    import yfinance as yf

    unique=sorted({t for t in tickers if t})
    if not unique:
        return [],[]
    limiter=StartRateLimiter(delay_seconds)
    observations=[]
    failures=[]
    workers=max(1,min(int(max_workers),len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures=[executor.submit(_collect_one,ticker,yf,limiter) for ticker in unique]
        for future in as_completed(futures):
            obs,failure=future.result()
            observations.extend(obs)
            if failure is not None:
                failures.append(failure)
    observations.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("field",""))))
    failures.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("reason",""))))
    return observations,failures
