from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import json
import math
import os
import re
import time
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "data/reference/V20.5_FUNNEL_CONFIG.json"
TARGET = ROOT / "outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv"
AUDIT = ROOT / "outputs/audit/V20.5_FUNNEL_CONTEXT.json"
CONTEXT_CSV = ROOT / "outputs/V20.5_ETF102_FUNNEL_CONTEXT.csv"
UA = "PEA-Analyzer-V20.5-Funnel/1.0"


def _float(v) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def _get(url: str, *, params: dict | None = None, timeout: int = 20, headers: dict | None = None) -> requests.Response:
    h = {"User-Agent": UA, "Accept": "application/json,text/csv,*/*"}
    if headers:
        h.update(headers)
    r = requests.get(url, params=params, headers=h, timeout=timeout)
    r.raise_for_status()
    return r


def _fred_observations(series_id: str, api_key: str, limit: int = 20) -> list[tuple[str, float]]:
    r = _get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": api_key, "file_type": "json", "sort_order": "desc", "limit": limit},
    )
    rows = []
    for item in r.json().get("observations", []):
        v = _float(item.get("value"))
        if v is not None:
            rows.append((str(item.get("date") or ""), v))
    return rows


def _us_macro(cfg: dict) -> dict:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return {"status": "NO_FRED_KEY"}
    fs = cfg["fred_series"]
    try:
        cpi = _fred_observations(fs["us_cpi"], key, 15)
        fed = _fred_observations(fs["fed_funds"], key, 8)
        y2 = _fred_observations(fs["us_2y"], key, 5)
        y10 = _fred_observations(fs["us_10y"], key, 5)
        un = _fred_observations(fs["us_unemployment"], key, 8)
        if len(cpi) < 13 or not fed or not y2 or not y10 or not un:
            raise RuntimeError("insufficient FRED observations")
        latest_cpi = cpi[0][1]
        cpi_12 = cpi[12][1]
        yoy = (latest_cpi / cpi_12 - 1.0) * 100.0 if cpi_12 else None
        fed_now, fed_old = fed[0][1], fed[min(3, len(fed)-1)][1]
        fed_dir = 70.0 if fed_now <= fed_old - .25 else (35.0 if fed_now >= fed_old + .25 else 50.0)
        market_bias_spread = y2[0][1] - fed_now
        fed_future = 70.0 if market_bias_spread <= -.50 else (35.0 if market_bias_spread >= .50 else 50.0)
        curve = y10[0][1] - y2[0][1]
        curve_score = 70.0 if curve >= .50 else (60.0 if curve >= 0 else 40.0)
        un_now, un_old = un[0][1], un[min(6, len(un)-1)][1]
        un_score = 35.0 if un_now >= un_old + .50 else (65.0 if un_now <= un_old - .25 else 50.0)
        inf_score = _clip(100.0 - abs((yoy if yoy is not None else 4.0) - 2.0) * 15.0, 20.0, 90.0)
        score = .30*inf_score + .25*fed_dir + .20*fed_future + .15*curve_score + .10*un_score
        return {
            "status": "OK", "score": round(score, 2), "cpi_yoy_pct": round(yoy, 3) if yoy is not None else None,
            "fed_funds_pct": fed_now, "fed_funds_3m_change_pp": round(fed_now-fed_old, 3),
            "us_2y_pct": y2[0][1], "us_10y_pct": y10[0][1], "curve_10y_2y_pp": round(curve, 3),
            "market_implied_fed_bias_2y_minus_fedfunds_pp": round(market_bias_spread, 3),
            "market_implied_fed_bias": "EASING" if market_bias_spread <= -.50 else ("TIGHTENING" if market_bias_spread >= .50 else "NEUTRAL"),
            "unemployment_pct": un_now,
            "source": "FRED_OFFICIAL_API",
        }
    except Exception as exc:
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _ecb_deposit(cfg: dict) -> dict:
    flow = cfg["ecb"]["deposit_rate_flow"]
    key = cfg["ecb"]["deposit_rate_key"]
    url = f"https://data-api.ecb.europa.eu/service/data/{flow}/{key}"
    try:
        r = _get(url, params={"lastNObservations": 8}, headers={"Accept": "text/csv"})
        df = pd.read_csv(StringIO(r.text))
        col = "OBS_VALUE" if "OBS_VALUE" in df.columns else next((c for c in df.columns if "VALUE" in c.upper()), None)
        if col is None:
            raise RuntimeError("ECB OBS_VALUE missing")
        vals = pd.to_numeric(df[col], errors="coerce").dropna().tolist()
        if not vals:
            raise RuntimeError("ECB rate empty")
        now, old = float(vals[-1]), float(vals[0])
        direction_score = 70.0 if now <= old - .25 else (35.0 if now >= old + .25 else 50.0)
        return {"status": "OK", "deposit_rate_pct": now, "recent_change_pp": round(now-old, 3), "direction_score": direction_score, "source": url}
    except Exception as exc:
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:200]}", "source": url}


def _eurostat_hicp(country: str, cfg: dict) -> dict:
    ecfg = cfg["eurostat"]
    url = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{ecfg['hicp_dataset']}"
    try:
        params = {"lang": "en", "unit": ecfg["unit"], "coicop": ecfg["coicop"], "geo": country}
        d = _get(url, params=params).json()
        values = d.get("value", {})
        time_dim = d.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
        if isinstance(time_dim, list):
            time_map = {v: i for i, v in enumerate(time_dim)}
        else:
            time_map = {str(k): int(v) for k, v in time_dim.items()}
        candidates = []
        for period, pos in time_map.items():
            raw = values.get(str(pos), values.get(pos))
            v = _float(raw)
            if v is not None:
                candidates.append((period, v))
        if not candidates:
            raise RuntimeError("Eurostat HICP empty")
        period, value = sorted(candidates, key=lambda x: x[0])[-1]
        score = _clip(100.0 - abs(value - 2.0) * 15.0, 20.0, 90.0)
        return {"status": "OK", "country": country, "hicp_yoy_pct": value, "period": period, "inflation_score": round(score, 2), "source": url}
    except Exception as exc:
        return {"status": "ERROR", "country": country, "error": f"{type(exc).__name__}: {str(exc)[:180]}", "source": url}


def _news_score(query: str, cfg: dict) -> dict:
    ncfg = cfg["news"]
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": int(ncfg["max_records"]),
        "sort": "HybridRel",
        "timespan": ncfg["timespan"],
    }
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    try:
        data = _get(url, params=params, timeout=25).json()
        articles = data.get("articles", []) or []
        risk_terms = [x.lower() for x in ncfg["risk_terms"]]
        pos_terms = [x.lower() for x in ncfg["positive_terms"]]
        article_scores = []
        for a in articles:
            title = str(a.get("title") or "").lower()
            neg = sum(1 for t in risk_terms if t in title)
            pos = sum(1 for t in pos_terms if t in title)
            article_scores.append(_clip(50.0 + 12.0*(pos-neg), 15.0, 85.0))
        score = float(np.mean(article_scores)) if article_scores else None
        return {"status": "OK" if articles else "NO_ARTICLES", "score": round(score, 2) if score is not None else None, "articles": len(articles), "query": query, "source": url}
    except Exception as exc:
        return {"status": "ERROR", "score": None, "articles": 0, "query": query, "error": f"{type(exc).__name__}: {str(exc)[:180]}", "source": url}


def _country_code(value: object, cfg: dict) -> str:
    s = re.sub(r"\s+", " ", str(value or "").upper()).strip()
    aliases = cfg["country_aliases"]
    for alias, code in aliases.items():
        if alias in s:
            return code
    return "GLOBAL"


def _weighted_context(values: dict[str, float | None], weights: dict[str, float]) -> tuple[float | None, float]:
    num = 0.0
    den = 0.0
    total = sum(weights.values())
    for key, weight in weights.items():
        v = values.get(key)
        if v is None or not math.isfinite(float(v)):
            continue
        num += float(v) * float(weight)
        den += float(weight)
    return (num / den if den > 0 else None), (den / total if total > 0 else 0.0)


def _sentiment_score(row: pd.Series) -> float | None:
    fg = _float(row.get("fear_greed_index"))
    spread = _float(row.get("aaii_bull_bear_spread"))
    pieces = []
    if fg is not None:
        if 45 <= fg <= 65:
            pieces.append(70.0)
        elif fg < 25:
            pieces.append(45.0)
        elif fg > 75:
            pieces.append(40.0)
        else:
            pieces.append(55.0)
    if spread is not None:
        pieces.append(65.0 if spread < -20 else (40.0 if spread > 30 else 58.0))
    return float(np.mean(pieces)) if pieces else None


def apply() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not TARGET.exists():
        raise RuntimeError(f"Missing ETF102 funnel target: {TARGET}")
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != 102 or df["isin"].astype(str).nunique() != 102:
        raise RuntimeError("Funnel requires exactly 102 validated ETFs")

    us = _us_macro(cfg)
    ecb = _ecb_deposit(cfg)
    country_codes = sorted({_country_code(v, cfg) for v in df.get("geo_exposure", pd.Series(["GLOBAL"]*len(df)))})
    euro_codes = [c for c in country_codes if c in {"FR","DE","IT","ES","NL","BE","AT","FI","PT","IE","GR"}]
    hicp = {c: _eurostat_hicp(c, cfg) for c in euro_codes}
    eu_inflation_scores = [x.get("inflation_score") for x in hicp.values() if x.get("status") == "OK"]
    eu_inf = float(np.mean(eu_inflation_scores)) if eu_inflation_scores else None
    eu_rate = ecb.get("direction_score") if ecb.get("status") == "OK" else None
    eu_score = None if eu_inf is None and eu_rate is None else float(np.mean([x for x in [eu_inf, eu_rate] if x is not None]))
    us_score = us.get("score") if us.get("status") == "OK" else None
    global_macro = float(np.mean([x for x in [us_score, eu_score] if x is not None])) if any(x is not None for x in [us_score, eu_score]) else None

    global_news = _news_score('(economy OR inflation OR interest rates OR recession OR growth OR central bank)', cfg)
    country_news: dict[str, dict] = {}
    for code in country_codes:
        if code == "GLOBAL":
            continue
        label = {"US":"United States","FR":"France","DE":"Germany","IT":"Italy","ES":"Spain","NL":"Netherlands","BE":"Belgium","AT":"Austria","FI":"Finland","PT":"Portugal","IE":"Ireland","GR":"Greece","EU":"Europe","JP":"Japan","IN":"India","CN":"China","EM":"emerging markets"}.get(code, code)
        country_news[code] = _news_score(f'"{label}" (economy OR inflation OR rates OR market)', cfg)
        time.sleep(.15)

    categories = df.get("category", pd.Series([""]*len(df))).fillna("").astype(str)
    top_categories = categories.value_counts().head(12).index.tolist()
    sector_news = {}
    for cat in top_categories:
        if not str(cat).strip():
            continue
        sector_news[str(cat)] = _news_score(f'"{str(cat)}" (stocks OR sector OR demand OR earnings)', cfg)
        time.sleep(.15)

    # True funnel: instrument-specific news is queried only for the strongest
    # preliminary technical candidates, never for the full 102 universe.
    p3 = pd.to_numeric(df.get("perf_3m_pct"), errors="coerce")
    p6 = pd.to_numeric(df.get("perf_6m_pct"), errors="coerce")
    rs = pd.to_numeric(df.get("relative_strength"), errors="coerce")
    prelim = p3.rank(pct=True).fillna(.5) + p6.rank(pct=True).fillna(.5) + rs.rank(pct=True).fillna(.5)
    top_n = int(cfg["news"]["top_instruments_for_specific_news"])
    specific_idx = set(prelim.nlargest(top_n).index.tolist())
    instrument_news: dict[str, dict] = {}
    for i in specific_idx:
        row = df.loc[i]
        isin = str(row.get("isin") or "")
        name = str(row.get("name") or "").strip()
        if name:
            instrument_news[isin] = _news_score(f'"{name}"', cfg)
            time.sleep(.12)

    context_rows = []
    weights = cfg["context_weights"]
    for i, row in df.iterrows():
        code = _country_code(row.get("geo_exposure"), cfg)
        if code == "US":
            country_macro = us_score
        elif code in hicp:
            inf = hicp[code].get("inflation_score") if hicp[code].get("status") == "OK" else None
            country_macro = float(np.mean([x for x in [inf, eu_rate] if x is not None])) if inf is not None or eu_rate is not None else None
        elif code == "EU":
            country_macro = eu_score
        else:
            country_macro = global_macro
        cnews = country_news.get(code, {}).get("score") if code != "GLOBAL" else global_news.get("score")
        cat = str(row.get("category") or "")
        snews = sector_news.get(cat, {}).get("score")
        inews = instrument_news.get(str(row.get("isin") or ""), {}).get("score")
        sentiment = _sentiment_score(row)
        vals = {
            "global_macro": global_macro,
            "country_macro": country_macro,
            "global_news": global_news.get("score"),
            "country_news": cnews,
            "sector_news": snews,
            "instrument_news": inews,
            "market_sentiment": sentiment,
        }
        score, coverage = _weighted_context(vals, weights)
        if score is None:
            multiplier = 1.0
            gate = "DATA_REQUIRED"
        else:
            lo, hi = float(cfg["context_multiplier"]["min"]), float(cfg["context_multiplier"]["max"])
            raw_mult = lo + (hi-lo) * score / 100.0
            if coverage < float(cfg["minimum_context_coverage_for_positive_multiplier"]) and raw_mult > 1.0:
                raw_mult = 1.0
            multiplier = raw_mult
            gate = "BLOCK_BUY" if score < float(cfg["risk_gates"]["block_buy_below"]) else ("REVIEW_ONLY" if score < float(cfg["risk_gates"]["review_only_below"]) else "PASS")
        context_rows.append({
            "isin": row.get("isin"), "funnel_country_code": code,
            "funnel_global_macro_score": global_macro, "funnel_country_macro_score": country_macro,
            "funnel_global_news_score": global_news.get("score"), "funnel_country_news_score": cnews,
            "funnel_sector_news_score": snews, "funnel_instrument_news_score": inews,
            "funnel_market_sentiment_score": sentiment, "funnel_context_score": score,
            "funnel_context_coverage": round(coverage, 4), "funnel_macro_multiplier": round(multiplier, 4),
            "funnel_risk_gate": gate,
        })
    ctx = pd.DataFrame(context_rows)
    out = df.drop(columns=[c for c in ctx.columns if c != "isin" and c in df.columns], errors="ignore").merge(ctx, on="isin", how="left")
    out.to_csv(TARGET, sep=";", index=False, encoding="utf-8-sig")
    ctx.to_csv(CONTEXT_CSV, sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "passed": True,
        "version": cfg["version"],
        "rows": len(out),
        "global_macro": {"score": global_macro, "us": us, "ecb": ecb, "eu_score": eu_score, "eurostat_hicp": hicp},
        "global_news": global_news,
        "country_news": country_news,
        "sector_news": sector_news,
        "instrument_news_queried": len(instrument_news),
        "mean_context_coverage": round(float(pd.to_numeric(ctx["funnel_context_coverage"], errors="coerce").mean()), 4),
        "risk_gates": ctx["funnel_risk_gate"].value_counts().to_dict(),
        "source_contracts": {"macro_us": "FRED", "inflation_eu": "EUROSTAT", "rates_euro_area": "ECB", "news": "GDELT", "sentiment": "CNN_FEAR_GREED+AAII"},
        "fed_future_semantics": "MARKET_IMPLIED_FROM_US_2Y_MINUS_FED_FUNDS_NOT_FOMC_PROMISE",
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> None:
    result = apply()
    print("V20.5_FUNNEL_CONTEXT_OK", json.dumps({"global_macro": result["global_macro"]["score"], "coverage": result["mean_context_coverage"], "gates": result["risk_gates"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
