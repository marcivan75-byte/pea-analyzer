from __future__ import annotations
import math
import time
import pandas as pd


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
    """Canonical direct diversification score retained by the ETF process.

    Existing Committee outputs show `diversification_direct_score` as exactly
    100 * (1 - direct_sector_hhi). Top-holdings concentration remains a separate
    structural criterion and is not blended into this canonical score, avoiding
    an undocumented double-counting formula.
    """
    if sector_hhi_value is None:
        return None
    return round(max(0.0,min(100.0,(1.0-float(sector_hhi_value))*100.0)),6)


def collect_fund_structure(tickers: list[str], delay_seconds: float = 0.2) -> tuple[list[dict], list[dict]]:
    """Collect ETF holdings/sector structure through yfinance `funds_data`.

    Missing or unsupported fund data remains missing and is returned in failures;
    no diversification score is imputed from category/name text.
    """
    import yfinance as yf
    observations=[]; failures=[]
    for ticker in sorted({t for t in tickers if t}):
        try:
            funds=yf.Ticker(ticker).funds_data
            top=getattr(funds,"top_holdings",None)
            sectors=getattr(funds,"sector_weightings",None)
            concentration=top_holdings_concentration_pct(top)
            hhi=sector_hhi(sectors)
            score=diversification_score(hhi)
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
                observations.extend([
                    {"ticker":ticker,"field":"direct_holdings_count","value":int(len(top)),"source":"yfinance.funds_data"},
                    {"ticker":ticker,"field":"top_holdings_observed_count","value":int(len(top)),"source":"yfinance.funds_data"},
                ])
            if concentration is None and hhi is None:
                failures.append({"ticker":ticker,"reason":"NO_FUND_STRUCTURE_DATA"})
        except Exception as exc:
            failures.append({"ticker":ticker,"reason":type(exc).__name__,"detail":str(exc)[:180]})
        time.sleep(delay_seconds)
    return observations,failures
