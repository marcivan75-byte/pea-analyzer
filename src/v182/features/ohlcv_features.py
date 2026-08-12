from __future__ import annotations
import numpy as np
import pandas as pd


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = -delta.clip(upper=0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _max_drawdown(close: pd.Series) -> float | None:
    clean = close.dropna()
    if clean.empty:
        return None
    drawdown = clean / clean.cummax() - 1
    return float(drawdown.min() * 100)


def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> tuple[pd.Series, pd.Series]:
    lowest = low.rolling(window).min()
    highest = high.rolling(window).max()
    denom = (highest - lowest).replace(0, np.nan)
    k = (close - lowest) / denom * 100.0
    d = k.rolling(3).mean()
    return k, d


def _dividend_features(frame: pd.DataFrame) -> dict[str, float | None]:
    """Derive observed distribution status and 3y dividend CAGR from cash dividends.

    CAGR compares current trailing-12-month cash dividends with the trailing-12-
    month window ending three years earlier. It is emitted only when both windows
    are strictly positive. Distribution policy is observed as 1/0 only when an
    explicit Dividends series exists with at least one year of history.
    """
    if "Dividends" not in frame.columns or not isinstance(frame.index,pd.DatetimeIndex):
        return {"distribution_policy":None,"dividend_cagr_3y":None,"dividend_ttm":None}
    dividends=pd.to_numeric(frame["Dividends"],errors="coerce").fillna(0.0)
    if dividends.empty:
        return {"distribution_policy":None,"dividend_cagr_3y":None,"dividend_ttm":None}
    end=pd.Timestamp(frame.index.max())
    if end.tzinfo is not None:
        end=end.tz_localize(None)
        dividends=dividends.copy(); dividends.index=dividends.index.tz_localize(None)
    start_current=end-pd.Timedelta(days=365)
    current=float(dividends[(dividends.index>start_current)&(dividends.index<=end)].sum())
    policy=1.0 if current>0 else 0.0 if (end-pd.Timestamp(dividends.index.min())).days>=365 else None
    old_end=end-pd.Timedelta(days=365*3)
    old_start=old_end-pd.Timedelta(days=365)
    old=float(dividends[(dividends.index>old_start)&(dividends.index<=old_end)].sum())
    cagr=((current/old)**(1/3)-1)*100.0 if current>0 and old>0 else None
    return {
        "distribution_policy":policy,
        "dividend_cagr_3y":round(float(cagr),6) if cagr is not None and np.isfinite(cagr) else None,
        "dividend_ttm":round(current,8),
    }


def calculate(frame: pd.DataFrame) -> dict:
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns): return {}
    frame = frame.sort_index().dropna(subset=["Close"])
    if frame.empty: return {}
    open_,close,high,low,volume=frame["Open"],frame["Close"],frame["High"],frame["Low"],frame["Volume"]
    result: dict[str, float | bool | None] = {}
    for window in (20,50,100,200): result[f"mm{window}"]=float(close.rolling(window).mean().iloc[-1]) if len(close)>=window else None
    result["rsi14"]=float(_rsi(close).iloc[-1]) if len(close)>=15 else None
    ema12,ema26=close.ewm(span=12,adjust=False).mean(),close.ewm(span=26,adjust=False).mean(); macd=ema12-ema26; signal=macd.ewm(span=9,adjust=False).mean(); hist=macd-signal
    result.update({"macd":float(macd.iloc[-1]),"macd_signal":float(signal.iloc[-1]),"macd_hist":float(hist.iloc[-1]),"macd_hist_3d_ago":float(hist.iloc[-4]) if len(hist)>=4 and pd.notna(hist.iloc[-4]) else None,"macd_hist_change_3d":float(hist.iloc[-1]-hist.iloc[-4]) if len(hist)>=4 and pd.notna(hist.iloc[-4]) else None})
    prev=close.shift(1); tr=pd.concat([high-low,(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1); atr=tr.rolling(14).mean()
    result["atr14"]=float(atr.iloc[-1]) if len(close)>=15 and pd.notna(atr.iloc[-1]) else None
    result["atr14_pct"]=float(atr.iloc[-1]/close.iloc[-1]*100) if result["atr14"] is not None and close.iloc[-1] else None
    result["opening_gap_pct"]=float((open_.iloc[-1]/prev.iloc[-1]-1)*100) if len(close)>=2 and pd.notna(open_.iloc[-1]) and pd.notna(prev.iloc[-1]) and prev.iloc[-1] else None
    mid=close.rolling(20).mean(); std=close.rolling(20).std(); upper=mid+2*std; lower=mid-2*std; bw=(upper-lower)/mid.replace(0,np.nan)
    result.update({"bb_mid":float(mid.iloc[-1]) if len(close)>=20 and pd.notna(mid.iloc[-1]) else None,"bb_upper":float(upper.iloc[-1]) if len(close)>=20 and pd.notna(upper.iloc[-1]) else None,"bb_lower":float(lower.iloc[-1]) if len(close)>=20 and pd.notna(lower.iloc[-1]) else None,"bb_bandwidth":float(bw.iloc[-1]) if len(close)>=20 and pd.notna(bw.iloc[-1]) else None})
    result["bb_breakout_cross_flag"]=bool(len(close)>=22 and pd.notna(upper.iloc[-1]) and pd.notna(upper.iloc[-2]) and close.iloc[-2]<=upper.iloc[-2] and close.iloc[-1]>upper.iloc[-1])
    result["bb_breakout_hold_flag"]=bool(len(close)>=23 and all(pd.notna(upper.iloc[-i]) and close.iloc[-i]>=upper.iloc[-i] for i in (1,2)))
    if len(bw.dropna())>=100:
        h100=bw.dropna().tail(100); p15=float(h100.quantile(.15)); last8=bw.dropna().tail(8); previous=bw.dropna().iloc[-2] if len(bw.dropna())>=2 else np.nan
        result.update({"bb_bandwidth_p15_100":p15,"bb_squeeze_fraction_8":float((last8<=p15).mean()) if len(last8)==8 else None,"bb_bandwidth_expansion_ratio":float(bw.iloc[-1]/previous) if pd.notna(previous) and previous>0 and pd.notna(bw.iloc[-1]) else None})
    else: result.update({"bb_bandwidth_p15_100":None,"bb_squeeze_fraction_8":None,"bb_bandwidth_expansion_ratio":None})
    k,d=_stochastic(high,low,close); result["stoch_k"]=float(k.iloc[-1]) if pd.notna(k.iloc[-1]) else None; result["stoch_d"]=float(d.iloc[-1]) if pd.notna(d.iloc[-1]) else None
    result["stoch_bull_cross_flag"]=bool(len(k)>=2 and pd.notna(k.iloc[-1]) and pd.notna(d.iloc[-1]) and pd.notna(k.iloc[-2]) and pd.notna(d.iloc[-2]) and k.iloc[-2]<=d.iloc[-2] and k.iloc[-1]>d.iloc[-1])
    avgv=volume.rolling(20).mean(); result["rvol20"]=float(volume.iloc[-1]/avgv.iloc[-1]) if len(close)>=20 and avgv.iloc[-1] else None; result["rvol20_3d_avg"]=float((volume/avgv.replace(0,np.nan)).tail(3).mean()) if len(close)>=22 else None
    daily=close.pct_change(); result["volatility_20d"]=float(daily.rolling(20).std().iloc[-1]*np.sqrt(252)*100) if len(close)>=21 else None; result["volatility_60d"]=float(daily.rolling(60).std().iloc[-1]*np.sqrt(252)*100) if len(close)>=61 else None; result["volatility_1y_pct"]=float(daily.rolling(252).std().iloc[-1]*np.sqrt(252)*100) if len(close)>=253 else None; result["max_drawdown_1y"]=_max_drawdown(close.tail(252))
    for field,days in {"perf_10d_pct":10,"perf_1m_pct":21,"perf_3m_pct":63,"perf_6m_pct":126,"perf_1y_pct":252,"perf_3y_pct":756,"perf_5y_pct":1260}.items(): result[field]=float((close.iloc[-1]/close.iloc[-days]-1)*100) if len(close)>=days else None
    result.update(_dividend_features(frame))
    result["positive_reversal_flag"]=bool(result.get("rsi14") is not None and 30<=result["rsi14"]<70 and result.get("macd_hist") is not None and result["macd_hist"]>0 and result.get("mm20") is not None and close.iloc[-1]>result["mm20"])
    result["last_close"]=float(close.iloc[-1]); result["volume"]=float(volume.iloc[-1])
    return result
