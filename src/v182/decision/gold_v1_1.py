from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import logging
import math
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CFTC_RESOURCE = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"


@dataclass(frozen=True)
class GoldRunResult:
    version: str
    tactical_score: float | None
    strategic_score: float | None
    tactical_coverage: float
    strategic_coverage: float
    qds_or: float | None
    data_trust: float | None
    output_path: str


def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clip(value: float) -> float:
    return float(np.clip(value, 0.0, 100.0))


def _percentile_score(values: pd.Series | list[float], *, inverse: bool = False) -> float | None:
    s = pd.Series(values, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(s) < 20:
        return None
    latest = float(s.iloc[-1])
    rank = float((s <= latest).mean() * 100.0)
    return round(100.0 - rank if inverse else rank, 4)


def _change_score(values: pd.Series | list[float], periods: int, *, inverse: bool = False) -> float | None:
    s = pd.Series(values, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(s) <= periods + 20:
        return None
    return _percentile_score(s.diff(periods).dropna(), inverse=inverse)


def _perf_series(series: pd.Series, sessions: int) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return s / s.shift(sessions) - 1.0


def _perf_score(series: pd.Series, sessions: int, *, inverse: bool = False) -> float | None:
    return _percentile_score(_perf_series(series, sessions), inverse=inverse)


def _quality_low(series: pd.Series) -> float | None:
    return _percentile_score(series, inverse=True)


def _max_drawdown_series(close: pd.Series, window: int) -> pd.Series:
    c = pd.to_numeric(close, errors="coerce")
    roll_max = c.rolling(window, min_periods=max(20, window // 3)).max()
    dd = c / roll_max - 1.0
    return dd.rolling(window, min_periods=max(20, window // 3)).min()


def _extract_ticker(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    if isinstance(frame.columns, pd.MultiIndex):
        level0 = set(map(str, frame.columns.get_level_values(0)))
        level1 = set(map(str, frame.columns.get_level_values(1))) if frame.columns.nlevels > 1 else set()
        try:
            if ticker in level0:
                out = frame[ticker].copy()
            elif ticker in level1:
                out = frame.xs(ticker, axis=1, level=1).copy()
            else:
                return pd.DataFrame()
        except Exception:
            return pd.DataFrame()
    else:
        out = frame.copy()
    out.columns = [str(c).lower().replace(" ", "_") for c in out.columns]
    return out.dropna(how="all")


def _download_market(cfg: dict) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    import yfinance as yf
    tickers = cfg["market"]["tickers"]
    values = list(dict.fromkeys(tickers.values()))
    status = []
    try:
        frame = yf.download(
            tickers=values, period=cfg["market"].get("history_period", "5y"), interval=cfg["market"].get("interval", "1d"),
            group_by="ticker", auto_adjust=True, actions=False, threads=True, progress=False, timeout=30,
        )
    except Exception as exc:
        return {}, [{"source": "yfinance", "status": "FAILED", "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}]
    out = {}
    for role, ticker in tickers.items():
        sub = _extract_ticker(frame, ticker)
        if sub.empty or "close" not in sub:
            status.append({"source": f"yfinance:{ticker}", "role": role, "status": "MISSING"})
        else:
            out[role] = sub
            status.append({"source": f"yfinance:{ticker}", "role": role, "status": "OK", "rows": int(len(sub)), "as_of": str(pd.Timestamp(sub.index[-1]).date())})
    return out, status


def _synthetic_xau_eur(market: dict[str, pd.DataFrame]) -> pd.DataFrame:
    gold = market.get("gold_usd_proxy")
    fx = market.get("eurusd")
    if gold is None or fx is None or gold.empty or fx.empty:
        return pd.DataFrame()
    cols = [c for c in ("open", "high", "low", "close") if c in gold.columns]
    aligned = gold[cols].join(fx[["close"]].rename(columns={"close": "eurusd"}), how="inner").dropna()
    if aligned.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=aligned.index)
    for c in cols:
        out[c] = aligned[c] / aligned["eurusd"]
    if "volume" in gold.columns:
        out["volume"] = pd.to_numeric(gold["volume"], errors="coerce").reindex(out.index)
    return out


def _fetch_fred(series_id: str, api_key: str | None, limit: int = 1200) -> tuple[pd.Series, str | None]:
    if not api_key:
        return pd.Series(dtype=float), "FRED_API_KEY_MISSING"
    import requests
    try:
        r = requests.get(FRED_OBSERVATIONS, params={"series_id": series_id, "api_key": api_key, "file_type": "json", "sort_order": "desc", "limit": limit}, timeout=20)
        r.raise_for_status()
        data = []
        for row in reversed(r.json().get("observations", [])):
            try:
                data.append((pd.Timestamp(row["date"]), float(row["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not data:
            return pd.Series(dtype=float), "NO_USABLE_OBSERVATIONS"
        return pd.Series({d: v for d, v in data}, dtype=float).sort_index(), None
    except Exception as exc:
        return pd.Series(dtype=float), f"{type(exc).__name__}: {str(exc)[:180]}"


def _fetch_ecb_usd(cfg: dict) -> tuple[float | None, str | None]:
    import requests
    try:
        r = requests.get(cfg["ecb"]["fx_reference_url"], timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for node in root.iter():
            if node.attrib.get("currency") == "USD":
                return _finite(node.attrib.get("rate")), None
        return None, "USD_RATE_NOT_FOUND"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:180]}"


def _first_key(row: dict, names: tuple[str, ...]) -> float | None:
    for name in names:
        if name in row:
            value = _finite(row.get(name))
            if value is not None:
                return value
    return None


def _fetch_cftc(cfg: dict) -> tuple[pd.DataFrame, str | None]:
    import requests
    code = cfg["cftc"]["contract_market_code"]
    params = {"$limit": int(cfg["cftc"].get("lookback_rows", 180)), "$order": "report_date_as_yyyy_mm_dd DESC", "$where": f"cftc_contract_market_code='{code}'"}
    try:
        r = requests.get(CFTC_RESOURCE, params=params, timeout=25)
        r.raise_for_status()
        parsed = []
        for row in r.json() if isinstance(r.json(), list) else []:
            long_ = _first_key(row, ("m_money_positions_long_all", "m_money_positions_long"))
            short = _first_key(row, ("m_money_positions_short_all", "m_money_positions_short"))
            oi = _first_key(row, ("open_interest_all",))
            date_raw = row.get("report_date_as_yyyy_mm_dd")
            if long_ is None or short is None or oi is None or not date_raw:
                continue
            parsed.append({"date": pd.Timestamp(date_raw), "long": long_, "short": short, "oi": oi})
        if not parsed:
            return pd.DataFrame(), "NO_USABLE_CFTC_ROWS"
        return pd.DataFrame(parsed).sort_values("date").reset_index(drop=True), None
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {str(exc)[:180]}"


def _gold_news(cfg: dict) -> tuple[dict[str, float], dict]:
    import requests
    params = {"query": "(gold OR bullion OR XAU)", "mode": "ArtList", "format": "json", "maxrecords": int(cfg["news"].get("max_records", 75)), "timespan": cfg["news"].get("timespan", "3d"), "sort": "HybridRel"}
    try:
        r = requests.get(GDELT_DOC, params=params, timeout=20)
        r.raise_for_status()
        articles = r.json().get("articles", [])
    except Exception as exc:
        return {}, {"source": "GDELT", "status": "FAILED", "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}
    titles = [str(a.get("title") or "").lower() for a in articles if isinstance(a, dict) and a.get("title")]
    if not titles:
        return {}, {"source": "GDELT", "status": "MISSING", "articles": 0}
    text = "\n".join(titles)
    stress_terms = ("war", "conflict", "sanction", "crisis", "uncertainty", "geopolitical", "attack", "tension", "tariff", "recession")
    bullish_terms = ("safe haven", "inflow", "central bank buy", "record high", "rally", "rate cut", "weaker dollar", "gold demand", "bullish")
    bearish_terms = ("outflow", "stronger dollar", "real yields rise", "selloff", "bearish", "liquidation", "profit taking")
    stress = sum(text.count(x) for x in stress_terms)
    bull = sum(text.count(x) for x in bullish_terms)
    bear = sum(text.count(x) for x in bearish_terms)
    evidence = bull + bear
    direction = None if evidence == 0 else _clip(50.0 + 15.0 * (bull - bear) / math.sqrt(evidence))
    domains = {str(a.get("domain") or "").lower() for a in articles if isinstance(a, dict) and a.get("domain")}
    scores = {
        "geopolitical_stress_score": _clip(35.0 + 10.0 * math.sqrt(stress)) if stress else 20.0,
        "gold_news_direction_score": direction,
        "gold_news_volume_score": _clip(len(titles) / max(1, int(cfg["news"].get("max_records", 75))) * 100.0),
        "safe_haven_news_score": _clip(20.0 + sum(text.count(x) for x in ("safe haven", "flight to safety", "hedge", "uncertainty")) * 12.0),
        "war_sanctions_stress_score": _clip(20.0 + sum(text.count(x) for x in ("war", "sanction", "attack", "conflict", "geopolitical")) * 8.0),
        "gold_sentiment_persistence": None if direction is None else _clip(100.0 - abs(direction - 50.0) * 0.5),
        "news_source_diversity": _clip(len(domains) / max(1, min(len(titles), 20)) * 100.0),
        "news_freshness": 100.0,
    }
    return {k: v for k, v in scores.items() if v is not None}, {"source": "GDELT", "status": "OK", "articles": len(titles), "domains": len(domains)}


def _load_manual_snapshot(root: Path, cfg: dict) -> tuple[dict[str, float], dict]:
    path = root / cfg["wgc"]["manual_snapshot_path"]
    if not path.exists():
        return {}, {"source": "GOLD_OFFICIAL_SNAPSHOT", "status": "MISSING", "path": str(path.relative_to(root))}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, {"source": "GOLD_OFFICIAL_SNAPSHOT", "status": "FAILED", "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}
    if not isinstance(raw, dict) or not raw.get("as_of") or not raw.get("source_url"):
        return {}, {"source": "GOLD_OFFICIAL_SNAPSHOT", "status": "REJECTED", "detail": "as_of and source_url required"}
    values = {}
    for key, value in (raw.get("criterion_scores") or {}).items():
        x = _finite(value)
        if x is not None and 0.0 <= x <= 100.0:
            values[str(key)] = x
    return values, {"source": "GOLD_OFFICIAL_SNAPSHOT", "status": "OK", "as_of": raw.get("as_of"), "source_url": raw.get("source_url"), "criteria": len(values)}


def _technical_scores(xau_eur: pd.DataFrame, market: dict[str, pd.DataFrame]) -> dict[str, float]:
    if xau_eur.empty or len(xau_eur) < 260:
        return {}
    from ta.momentum import RSIIndicator
    from ta.trend import MACD, ADXIndicator
    from ta.volatility import AverageTrueRange, BollingerBands
    c = pd.to_numeric(xau_eur["close"], errors="coerce")
    h = pd.to_numeric(xau_eur.get("high", c), errors="coerce")
    l = pd.to_numeric(xau_eur.get("low", c), errors="coerce")
    o = pd.to_numeric(xau_eur.get("open", c.shift(1)), errors="coerce")
    ret = c.pct_change()
    scores: dict[str, float | None] = {}
    for window in (20, 50, 100, 200):
        sma = c.rolling(window).mean()
        scores[f"xau_eur_trend_sma{window}"] = _percentile_score(c / sma - 1.0)
    for window, lag in ((20, 10), (50, 20), (200, 60)):
        sma = c.rolling(window).mean()
        scores[f"xau_eur_slope_sma{window}"] = _percentile_score(sma / sma.shift(lag) - 1.0)
    rsi = RSIIndicator(c, window=14).rsi()
    rsi_now = _finite(rsi.iloc[-1])
    scores["xau_eur_rsi14_quality"] = None if rsi_now is None else _clip(100.0 - abs(rsi_now - 58.0) * 2.2)
    scores["xau_eur_macd_hist"] = _percentile_score(MACD(c).macd_diff())
    scores["xau_eur_adx14"] = _percentile_score(ADXIndicator(h, l, c, window=14).adx())
    scores["xau_eur_atr14_quality"] = _quality_low(AverageTrueRange(h, l, c, window=14).average_true_range() / c)
    for window in (20, 60):
        scores[f"xau_eur_support{window}_distance"] = _quality_low(c / c.rolling(window).min() - 1.0)
        scores[f"xau_eur_resistance{window}_breakout"] = _percentile_score(c / c.shift(1).rolling(window).max() - 1.0)
    bb = BollingerBands(c, window=20, window_dev=2)
    width = (bb.bollinger_hband() - bb.bollinger_lband()) / c
    position = (c - bb.bollinger_lband()) / (bb.bollinger_hband() - bb.bollinger_lband()).replace(0, np.nan)
    scores["xau_eur_bollinger_position"] = _percentile_score(position)
    scores["xau_eur_bollinger_width_quality"] = _quality_low(width)
    align = ((c > c.rolling(20).mean()).astype(int) + (c.rolling(20).mean() > c.rolling(50).mean()).astype(int) + (c.rolling(50).mean() > c.rolling(100).mean()).astype(int) + (c.rolling(100).mean() > c.rolling(200).mean()).astype(int)) / 4.0 * 100.0
    scores["xau_eur_trend_alignment"] = _finite(align.iloc[-1])
    for name, sessions in (("1w", 5), ("1m", 21), ("3m", 63), ("6m", 126), ("12m", 252), ("24m", 504)):
        scores[f"xau_eur_perf_{name}"] = _perf_score(c, sessions)
    gold_usd = market.get("gold_usd_proxy")
    if gold_usd is not None and not gold_usd.empty:
        gc = pd.to_numeric(gold_usd["close"], errors="coerce")
        scores["xau_usd_perf_1m"] = _perf_score(gc, 21)
        scores["xau_usd_perf_3m"] = _perf_score(gc, 63)
    scores["momentum_acceleration_1m_3m"] = _percentile_score(_perf_series(c, 21) - _perf_series(c, 63) / 3.0)
    scores["positive_days_20"] = _finite(ret.gt(0).rolling(20).mean().iloc[-1] * 100.0)
    scores["positive_days_60"] = _finite(ret.gt(0).rolling(60).mean().iloc[-1] * 100.0)
    scores["breakout_252"] = _percentile_score(c / c.shift(1).rolling(252).max() - 1.0)
    ann = math.sqrt(252.0)
    for window in (20, 60, 252):
        scores[f"xau_eur_vol{window}_quality"] = _quality_low(ret.rolling(window).std() * ann)
    scores["xau_eur_maxdd_3m_quality"] = _percentile_score(_max_drawdown_series(c, 63))
    scores["xau_eur_maxdd_1y_quality"] = _percentile_score(_max_drawdown_series(c, 252))
    scores["xau_eur_current_drawdown_quality"] = _percentile_score(c / c.rolling(252).max() - 1.0)
    vix = market.get("vix")
    if vix is not None and not vix.empty:
        scores["vix_regime_gold_support"] = _percentile_score(pd.to_numeric(vix["close"], errors="coerce"))
    sp = market.get("sp500")
    if sp is not None and not sp.empty:
        joined = pd.concat([c.rename("gold"), pd.to_numeric(sp["close"], errors="coerce").rename("sp")], axis=1).dropna()
        if len(joined) >= 260:
            gr, sr = joined["gold"].pct_change(), joined["sp"].pct_change()
            scores["gold_corr_sp500_diversification"] = _percentile_score(gr.rolling(252).corr(sr).abs(), inverse=True)
            scores["gold_beta_sp500_quality"] = _percentile_score((gr.rolling(252).cov(sr) / sr.rolling(252).var()).abs(), inverse=True)
    scores["gap_risk_quality"] = _quality_low((o / c.shift(1) - 1.0).abs())
    return {k: _clip(v) for k, v in scores.items() if v is not None and math.isfinite(float(v))}


def _macro_scores(cfg: dict, fred_key: str | None, market: dict[str, pd.DataFrame], ecb_usd: float | None) -> tuple[dict[str, float], list[dict]]:
    scores: dict[str, float] = {}
    status = []
    series = {}
    for role, sid in cfg["fred"]["series"].items():
        s, error = _fetch_fred(sid, fred_key)
        series[role] = s
        status.append({"source": f"FRED:{sid}", "role": role, "status": "FAILED" if error else "OK", "detail": error, "rows": int(len(s))})
    ry10, ry5, ry30 = series.get("real_yield_10y", pd.Series(dtype=float)), series.get("real_yield_5y", pd.Series(dtype=float)), series.get("real_yield_30y", pd.Series(dtype=float))
    if not ry10.empty:
        scores["real_yield_10y_level"] = _percentile_score(ry10, inverse=True)
        scores["real_yield_10y_change_1m"] = _change_score(ry10, 21, inverse=True)
        scores["real_yield_10y_change_3m"] = _change_score(ry10, 63, inverse=True)
    if not ry5.empty:
        scores["real_yield_5y_level"] = _percentile_score(ry5, inverse=True)
        scores["real_yield_5y_change_1m"] = _change_score(ry5, 21, inverse=True)
    if not ry30.empty:
        scores["real_yield_30y_level"] = _percentile_score(ry30, inverse=True)
    if not ry5.empty and not ry10.empty:
        a = pd.concat([ry5.rename("a"), ry10.rename("b")], axis=1).dropna()
        scores["real_curve_5s10s"] = _percentile_score(a["b"] - a["a"], inverse=True)
    if not ry10.empty and not ry30.empty:
        a = pd.concat([ry10.rename("a"), ry30.rename("b")], axis=1).dropna()
        scores["real_curve_10s30s"] = _percentile_score(a["b"] - a["a"], inverse=True)
    be10, be5 = series.get("breakeven_10y", pd.Series(dtype=float)), series.get("breakeven_5y", pd.Series(dtype=float))
    if not be10.empty:
        scores["breakeven_10y_level"] = _percentile_score(be10)
        scores["breakeven_10y_change_1m"] = _change_score(be10, 21)
    if not be5.empty:
        scores["breakeven_5y_level"] = _percentile_score(be5)
    cpi = series.get("cpi", pd.Series(dtype=float))
    if len(cpi) >= 36:
        yoy = cpi / cpi.shift(12) - 1.0
        scores["cpi_yoy_level"] = _percentile_score(yoy)
        scores["cpi_momentum"] = _percentile_score(yoy.diff(3))
    nominal = series.get("nominal_10y", pd.Series(dtype=float))
    if not nominal.empty and not ry10.empty:
        a = pd.concat([nominal.rename("n"), ry10.rename("r")], axis=1).dropna()
        scores["nominal_real_spread"] = _percentile_score(a["n"] - a["r"])
    macro_parts = [scores.get(k) for k in ("real_yield_10y_level", "breakeven_10y_level", "cpi_yoy_level") if scores.get(k) is not None]
    if macro_parts:
        scores["macro_gold_regime"] = float(np.mean(macro_parts))
    dxy = market.get("dxy")
    if dxy is not None and not dxy.empty:
        d = pd.to_numeric(dxy["close"], errors="coerce")
        scores["dxy_level_quality"] = _percentile_score(d, inverse=True)
        scores["dxy_perf_1m_inverse"] = _perf_score(d, 21, inverse=True)
        scores["dxy_perf_3m_inverse"] = _perf_score(d, 63, inverse=True)
        scores["dxy_perf_6m_inverse"] = _perf_score(d, 126, inverse=True)
    fx = market.get("eurusd")
    if fx is not None and not fx.empty:
        f = pd.to_numeric(fx["close"], errors="coerce")
        scores["eurusd_perf_1m"] = _perf_score(f, 21)
        scores["eurusd_perf_3m"] = _perf_score(f, 63)
        market_fx = _finite(f.iloc[-1])
        if market_fx is not None and ecb_usd is not None and ecb_usd > 0:
            diff = abs(market_fx / ecb_usd - 1.0)
            scores["ecb_fx_crosscheck_quality"] = 100.0 if diff <= 0.001 else 80.0 if diff <= 0.0025 else 50.0 if diff <= 0.005 else 0.0
    gold = market.get("gold_usd_proxy")
    if gold is not None and fx is not None and not gold.empty and not fx.empty:
        gc = pd.to_numeric(gold["close"], errors="coerce")
        eur = gc / pd.to_numeric(fx["close"], errors="coerce").reindex(gc.index)
        scores["xau_usd_xau_eur_convergence"] = _quality_low((_perf_series(gc, 21) - _perf_series(eur, 21)).abs())
    return {k: _clip(v) for k, v in scores.items() if v is not None}, status


def _cftc_scores(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty or len(frame) < 20:
        return {}
    net = frame["long"] - frame["short"]
    pct = net / frame["oi"].replace(0, np.nan)
    scores = {
        "cftc_managed_money_net": _percentile_score(net), "cftc_managed_money_net_pct_oi": _percentile_score(pct),
        "cftc_managed_money_change_1w": _percentile_score(net.diff(1)), "cftc_managed_money_change_4w": _percentile_score(net.diff(4)),
        "cftc_net_percentile_3y": _percentile_score(net.tail(156)), "cftc_open_interest_trend": _percentile_score(frame["oi"].pct_change(4)),
    }
    p = _percentile_score(net.tail(156))
    if p is not None:
        scores["cftc_crowding_quality"] = _clip(100.0 - abs(p - 50.0) * 1.6)
    latest = pd.Timestamp(frame["date"].iloc[-1]).tz_localize(None).normalize()
    age = (pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - latest).days
    scores["cftc_source_freshness"] = 100.0 if age <= 7 else 80.0 if age <= 14 else 40.0 if age <= 28 else 0.0
    return {k: _clip(v) for k, v in scores.items() if v is not None}


def _flow_and_crossasset_scores(market: dict[str, pd.DataFrame], xau_eur: pd.DataFrame) -> dict[str, float]:
    scores: dict[str, float] = {}
    proxy_scores = []
    for role, key in (("gld", "gld_price_volume_flow_proxy"), ("iau", "iau_price_volume_flow_proxy")):
        frame = market.get(role)
        if frame is None or frame.empty or "close" not in frame:
            continue
        close = pd.to_numeric(frame["close"], errors="coerce")
        p = _perf_score(close, 21)
        volume_score = None
        if "volume" in frame:
            vol = pd.to_numeric(frame["volume"], errors="coerce")
            volume_score = _percentile_score(vol.rolling(20).mean() / vol.rolling(126).mean())
        parts = [x for x in (p, volume_score) if x is not None]
        if parts:
            scores[key] = float(np.mean(parts)); proxy_scores.append(scores[key])
    if proxy_scores:
        scores["gold_etf_flow_source_quality"] = 65.0
    gold, silver, sp, gld, iau = market.get("gold_usd_proxy"), market.get("silver"), market.get("sp500"), market.get("gld"), market.get("iau")
    if gold is not None and silver is not None and not gold.empty and not silver.empty:
        ratio = pd.to_numeric(gold["close"], errors="coerce") / pd.to_numeric(silver["close"], errors="coerce").reindex(gold.index)
        scores["gold_silver_ratio_signal"] = _perf_score(ratio, 63)
    if gold is not None and sp is not None and not gold.empty and not sp.empty:
        ratio = pd.to_numeric(gold["close"], errors="coerce") / pd.to_numeric(sp["close"], errors="coerce").reindex(gold.index)
        scores["gold_sp500_ratio_signal"] = _perf_score(ratio, 63)
    if gld is not None and iau is not None and not gld.empty and not iau.empty:
        scores["gld_iau_convergence"] = _quality_low((_perf_series(pd.to_numeric(gld["close"], errors="coerce"), 21) - _perf_series(pd.to_numeric(iau["close"], errors="coerce"), 21)).abs())
    if not xau_eur.empty and sp is not None and not sp.empty:
        joined = pd.concat([xau_eur["close"].rename("g"), pd.to_numeric(sp["close"], errors="coerce").rename("s")], axis=1).dropna()
        if len(joined) >= 260:
            div = _percentile_score(joined["g"].pct_change().rolling(252).corr(joined["s"].pct_change()).abs(), inverse=True)
            if div is not None: scores["portfolio_diversification_signal"] = div
    parts = [scores.get(k) for k in ("gold_silver_ratio_signal", "gold_sp500_ratio_signal", "gld_iau_convergence") if scores.get(k) is not None]
    if parts:
        scores["market_implied_gold_consensus"] = float(np.mean(parts))
    return {k: _clip(v) for k, v in scores.items() if v is not None}


def _score_horizon(cfg: dict, values: dict[str, float], horizon: str) -> dict:
    weight_key = "tactical_weight" if horizon == "TACTICAL_2_12W" else "strategic_weight"
    total = sum(float(c.get(weight_key, 0.0) or 0.0) for c in cfg["criteria"])
    weighted = available_weight = 0.0; available = 0; contributions = []
    for criterion in cfg["criteria"]:
        w = float(criterion.get(weight_key, 0.0) or 0.0)
        if w <= 0: continue
        value = _finite(values.get(criterion["name"]))
        if value is None: continue
        value = _clip(value); weighted += value * w; available_weight += w; available += 1
        contributions.append({"criterion": criterion["name"], "block": criterion["block"], "score": round(value, 4), "weight": w, "contribution": round(value * w, 6)})
    coverage = available_weight / total if total else 0.0
    score = weighted / available_weight if available_weight else None
    minimum = float(cfg["horizons"][horizon]["minimum_weighted_coverage"])
    status = "SCORABLE" if score is not None and coverage >= minimum else "BLOCK_DATA"
    contributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)
    return {"score": round(float(score), 4) if score is not None else None, "coverage_pct": round(coverage * 100.0, 2), "status": status, "available_criteria": available, "active_criteria": sum(1 for c in cfg["criteria"] if float(c.get(weight_key, 0.0) or 0.0) > 0), "top_factors": contributions[:10]}


def _block_scores(cfg: dict, values: dict[str, float], horizon: str) -> dict[str, float]:
    weight_key = "tactical_weight" if horizon == "TACTICAL_2_12W" else "strategic_weight"; out = {}
    for block in cfg["horizons"][horizon]["block_weights"]:
        rows = [c for c in cfg["criteria"] if c["block"] == block and float(c.get(weight_key, 0.0) or 0.0) > 0]
        numer = denom = 0.0
        for c in rows:
            value = _finite(values.get(c["name"]))
            if value is None: continue
            w = float(c[weight_key]); numer += _clip(value) * w; denom += w
        if denom: out[block] = round(numer / denom, 4)
    return out


def _quality_metrics(cfg: dict, values: dict[str, float], source_status: list[dict], tactical_coverage: float) -> tuple[float | None, float | None, dict]:
    grade_scores = cfg["quality"]["source_grade_score"]
    available_criteria = [c for c in cfg["criteria"] if _finite(values.get(c["name"])) is not None and float(c.get("tactical_weight", 0.0) or 0.0) > 0]
    if not available_criteria: return None, None, {}
    source_quality = float(np.average([grade_scores.get(c.get("evidence_grade", "C"), 65.0) for c in available_criteria], weights=[max(float(c.get("tactical_weight", 0.0)), 1e-9) for c in available_criteria]))
    statuses = [s for s in source_status if s.get("status") in {"OK", "MISSING", "FAILED", "REJECTED"}]
    freshness = _clip(sum(1 for s in statuses if s.get("status") == "OK") / max(1, len(statuses)) * 100.0)
    convergence = values.get("xau_usd_xau_eur_convergence") or values.get("gld_iau_convergence")
    completeness = tactical_coverage * 100.0
    qw = cfg["quality"]["qds_or"]["weights"]
    qds = None if convergence is None else source_quality * qw["source_quality"] + freshness * qw["freshness"] + float(convergence) * qw["convergence"] + completeness * qw["completeness"]
    dw = cfg["quality"]["data_trust"]["weights"]
    identity = 100.0 if "xau_eur_trend_sma200" in values else 0.0; traceability = 100.0; conv = float(convergence) if convergence is not None else 0.0
    data_trust = source_quality * dw["sources"] + freshness * dw["freshness"] + conv * dw["convergence_independence"] + completeness * dw["completeness"] + identity * dw["identity_place_currency"] + traceability * dw["traceability"]
    return (round(qds, 4) if qds is not None else None, round(data_trust, 4), {"source_quality": round(source_quality, 4), "freshness": round(freshness, 4), "convergence": round(float(convergence), 4) if convergence is not None else None, "completeness": round(completeness, 4), "identity_place_currency": identity, "traceability": traceability})


def _decision_tactical(cfg: dict, scored: dict, qds: float | None, data_trust: float | None, below_sma200: bool) -> tuple[str, list[str]]:
    reasons = []
    if scored["status"] != "SCORABLE": return "ABSTAIN_COVERAGE", ["COVERAGE_LT_70"]
    if data_trust is None or data_trust < cfg["quality"]["data_trust"]["committee_min"]: return "ABSTAIN_DATA_TRUST", ["DATA_TRUST_LT_70"]
    score = float(scored["score"]); h = cfg["horizons"]["TACTICAL_2_12W"]
    if qds is None or qds < cfg["quality"]["qds_or"]["buy_reinforce_min"]: reasons.append("QDS_LT_70_NO_BUY_REINFORCE")
    if score < h["protect_threshold"] and below_sma200: return "SHADOW_PROTECT", reasons + ["SCORE_LT_35_AND_MM200_BREAK"]
    if score < h["strategic_hold_threshold"]: return "SHADOW_TRIM_TACTICAL", reasons
    if score < h["watch_threshold"]: return "SHADOW_STRATEGIC_HOLD", reasons
    if score < h["buy_threshold"]: return "SHADOW_WATCH_MICRO", reasons
    if qds is None or qds < cfg["quality"]["qds_or"]["buy_reinforce_min"]: return "SHADOW_WATCH_QDS", reasons
    return "SHADOW_BUY_TACTICAL", reasons


def _decision_strategic(cfg: dict, scored: dict, data_trust: float | None) -> tuple[str, list[str]]:
    if scored["status"] != "SCORABLE": return "ABSTAIN_COVERAGE", ["COVERAGE_LT_70"]
    if data_trust is None or data_trust < cfg["quality"]["data_trust"]["committee_min"]: return "ABSTAIN_DATA_TRUST", ["DATA_TRUST_LT_70"]
    s = float(scored["score"]); t = cfg["horizons"]["STRATEGIC_6_24M"]["decision_thresholds"]
    return ("SHADOW_STRATEGIC_FAVORABLE", []) if s >= t["favorable"] else ("SHADOW_STRATEGIC_NEUTRAL", []) if s >= t["neutral"] else ("SHADOW_STRATEGIC_DEFENSIVE", [])


def _write_history(root: Path, cfg: dict, payload: dict) -> None:
    path = root / cfg["outputs"]["history_csv"]; path.parent.mkdir(parents=True, exist_ok=True)
    row = {"generated_at_utc": payload["generated_at_utc"], "version": payload["version"], "gold_score_ct": payload["GOLD_SCORE_CT"], "gold_score_mt": payload["GOLD_SCORE_MT"], "tactical_coverage_pct": payload["current_scores"]["TACTICAL_2_12W"]["coverage_pct"], "strategic_coverage_pct": payload["current_scores"]["STRATEGIC_6_24M"]["coverage_pct"], "qds_or": payload["QDS_OR"], "data_trust": payload["DATA_TRUST_OR"], "tactical_decision": payload["current_scores"]["TACTICAL_2_12W"]["decision"], "strategic_decision": payload["current_scores"]["STRATEGIC_6_24M"]["decision"]}
    current = pd.read_csv(path, sep=";", encoding="utf-8-sig") if path.exists() else pd.DataFrame()
    pd.concat([current, pd.DataFrame([row])], ignore_index=True).tail(5000).to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def run(root: Path, fred_api_key: str | None = None) -> GoldRunResult:
    cfg_path = root / "config" / "GOLD_V1_1_102_CRITERIA.json"; cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if len(cfg.get("criteria", [])) != 102 or cfg["governance"]["criteria_count"] != 102: raise RuntimeError("GOLD_REGISTRY_INTEGRITY_102_FAILED")
    if abs(sum(cfg["horizons"]["TACTICAL_2_12W"]["block_weights"].values()) - 1.0) > 1e-9: raise RuntimeError("GOLD_TACTICAL_BLOCK_WEIGHTS_NOT_100")
    if abs(sum(cfg["horizons"]["STRATEGIC_6_24M"]["block_weights"].values()) - 1.0) > 1e-9: raise RuntimeError("GOLD_STRATEGIC_BLOCK_WEIGHTS_NOT_100")
    market, source_status = _download_market(cfg); xau_eur = _synthetic_xau_eur(market); values = _technical_scores(xau_eur, market)
    ecb_usd, ecb_error = _fetch_ecb_usd(cfg); source_status.append({"source": "ECB:EURUSD_REFERENCE", "status": "FAILED" if ecb_error else "OK", "detail": ecb_error, "value": ecb_usd})
    macro, fred_status = _macro_scores(cfg, fred_api_key, market, ecb_usd); values.update(macro); source_status.extend(fred_status)
    cftc, cftc_error = _fetch_cftc(cfg); source_status.append({"source": "CFTC:72hh-3qpy:GOLD_COMEX_088691", "status": "FAILED" if cftc_error else "OK", "detail": cftc_error, "rows": int(len(cftc))}); values.update(_cftc_scores(cftc))
    news, news_status = _gold_news(cfg); values.update(news); source_status.append(news_status)
    values.update(_flow_and_crossasset_scores(market, xau_eur)); manual, manual_status = _load_manual_snapshot(root, cfg); values.update(manual); source_status.append(manual_status)
    if manual: values["gold_etf_flow_source_quality"] = 100.0
    provisional_parts = [values.get(k) for k in ("xau_eur_perf_3m", "real_yield_10y_level", "dxy_perf_3m_inverse", "cftc_managed_money_net_pct_oi", "geopolitical_stress_score", "market_implied_gold_consensus") if values.get(k) is not None]
    if provisional_parts: values["gold_regime_consensus"] = float(np.mean(provisional_parts))
    tactical = _score_horizon(cfg, values, "TACTICAL_2_12W"); strategic = _score_horizon(cfg, values, "STRATEGIC_6_24M")
    qds, data_trust, qdetail = _quality_metrics(cfg, values, source_status, tactical["coverage_pct"] / 100.0)
    tactical_decision, tactical_reasons = _decision_tactical(cfg, tactical, qds, data_trust, values.get("xau_eur_trend_sma200", 100.0) < 50.0)
    strategic_decision, strategic_reasons = _decision_strategic(cfg, strategic, data_trust)
    tactical.update({"decision": tactical_decision, "reason_codes": tactical_reasons, "block_scores": _block_scores(cfg, values, "TACTICAL_2_12W")}); strategic.update({"decision": strategic_decision, "reason_codes": strategic_reasons, "block_scores": _block_scores(cfg, values, "STRATEGIC_6_24M")})
    generated = datetime.now(timezone.utc).isoformat(); output_dir = root / cfg["outputs"]["directory"]; output_dir.mkdir(parents=True, exist_ok=True)
    criteria_rows = []
    for c in cfg["criteria"]:
        value = _finite(values.get(c["name"])); criteria_rows.append({**c, "current_score": round(value, 4) if value is not None else None, "criterion_status": "AVAILABLE" if value is not None else "MISSING"})
    pd.DataFrame(criteria_rows).to_csv(output_dir / cfg["outputs"]["criteria_csv"], sep=";", index=False, encoding="utf-8-sig"); pd.DataFrame(source_status).to_csv(output_dir / cfg["outputs"]["source_status_csv"], sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "version": cfg["version"], "status": cfg["status"], "generated_at_utc": generated, "asset_class": "GOLD", "pea_eligible": False, "live_orders_enabled": False,
        "criteria_count": 102, "contribution_blocks": 11, "primary_price": "XAU_EUR_SYNTHETIC_FROM_COMEX_PROXY_AND_EURUSD", "primary_price_caveat": cfg["market"]["gold_usd_proxy_note"],
        "GOLD_SCORE_CT": tactical["score"], "GOLD_SCORE_MT": strategic["score"], "QDS_OR": qds, "DATA_TRUST_OR": data_trust, "quality_components": qdetail,
        "current_scores": {"TACTICAL_2_12W": tactical, "STRATEGIC_6_24M": strategic},
        "hard_gates": {"tactical_minimum_coverage_70": tactical["coverage_pct"] >= 70.0, "strategic_minimum_coverage_70": strategic["coverage_pct"] >= 70.0, "qds_min_70_for_buy_reinforce": qds is not None and qds >= 70.0, "data_trust_min_70_for_committee": data_trust is not None and data_trust >= 70.0, "real_orders_forbidden": True, "t1_t2_forbidden": True},
        "source_status": source_status, "top_factors": {"TACTICAL_2_12W": tactical["top_factors"], "STRATEGIC_6_24M": strategic["top_factors"]},
        "governance": {"top_level_family_weights_source_preserved": True, "intra_block_weights_provisional_not_optimised": True, "neutral_imputation_forbidden": True, "performance_claim": "NONE_FOR_V1_1_UNTIL_DEDICATED_PIT_BACKTEST", "historical_thresholds_are_versioned_not_performance_certification": True}
    }
    decision_path = output_dir / cfg["outputs"]["decision_json"]; decision_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"); _write_history(root, cfg, payload)
    return GoldRunResult(cfg["version"], tactical["score"], strategic["score"], tactical["coverage_pct"], strategic["coverage_pct"], qds, data_trust, str(decision_path.relative_to(root)))
