from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd

from v182.sources.funnel_context import (
    CONFIG,
    TARGET,
    AUDIT,
    CONTEXT_CSV,
    _country_code,
    _ecb_deposit,
    _eurostat_hicp,
    _news_score,
    _sentiment_score,
    _us_macro,
    _weighted_context,
)


def _parallel_news(jobs: dict[str, str], cfg: dict, workers: int = 10) -> dict[str, dict]:
    if not jobs:
        return {}
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs)))) as pool:
        future_map = {pool.submit(_news_score, query, cfg): key for key, query in jobs.items()}
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                out[key] = future.result()
            except Exception as exc:  # fail-safe: coverage decreases, pipeline continues
                out[key] = {"status": "ERROR", "score": None, "articles": 0, "query": jobs[key], "error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    return out


def apply() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not TARGET.exists():
        raise RuntimeError(f"Missing ETF102 funnel target: {TARGET}")
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != 102 or df["isin"].astype(str).nunique() != 102:
        raise RuntimeError("Funnel requires exactly 102 validated ETFs")

    # Stage 1-4: macro, inflation and rates.
    us = _us_macro(cfg)
    ecb = _ecb_deposit(cfg)
    country_codes = sorted({_country_code(v, cfg) for v in df.get("geo_exposure", pd.Series(["GLOBAL"] * len(df)))})
    euro_codes = [c for c in country_codes if c in {"FR", "DE", "IT", "ES", "NL", "BE", "AT", "FI", "PT", "IE", "GR"}]
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(euro_codes) or 1))) as pool:
        futures = {pool.submit(_eurostat_hicp, c, cfg): c for c in euro_codes}
        hicp = {futures[f]: f.result() for f in as_completed(futures)} if futures else {}

    eu_inflation_scores = [x.get("inflation_score") for x in hicp.values() if x.get("status") == "OK"]
    eu_inf = float(np.mean(eu_inflation_scores)) if eu_inflation_scores else None
    eu_rate = ecb.get("direction_score") if ecb.get("status") == "OK" else None
    eu_score = None if eu_inf is None and eu_rate is None else float(np.mean([x for x in [eu_inf, eu_rate] if x is not None]))
    us_score = us.get("score") if us.get("status") == "OK" else None
    global_macro = float(np.mean([x for x in [us_score, eu_score] if x is not None])) if any(x is not None for x in [us_score, eu_score]) else None

    # Stage 5-8: news, collected concurrently. Instrument news is deliberately
    # limited to the preliminary top 30 to preserve the funnel architecture.
    global_news = _news_score('(economy OR inflation OR interest rates OR recession OR growth OR central bank)', cfg)
    labels = {"US":"United States","FR":"France","DE":"Germany","IT":"Italy","ES":"Spain","NL":"Netherlands","BE":"Belgium","AT":"Austria","FI":"Finland","PT":"Portugal","IE":"Ireland","GR":"Greece","EU":"Europe","JP":"Japan","IN":"India","CN":"China","EM":"emerging markets"}
    country_jobs = {code: f'"{labels.get(code, code)}" (economy OR inflation OR rates OR market)' for code in country_codes if code != "GLOBAL"}
    country_news = _parallel_news(country_jobs, cfg)

    categories = df.get("category", pd.Series([""] * len(df))).fillna("").astype(str)
    top_categories = [str(x) for x in categories.value_counts().head(12).index.tolist() if str(x).strip()]
    sector_jobs = {cat: f'"{cat}" (stocks OR sector OR demand OR earnings)' for cat in top_categories}
    sector_news = _parallel_news(sector_jobs, cfg)

    p3 = pd.to_numeric(df.get("perf_3m_pct"), errors="coerce")
    p6 = pd.to_numeric(df.get("perf_6m_pct"), errors="coerce")
    rs = pd.to_numeric(df.get("relative_strength"), errors="coerce")
    prelim = p3.rank(pct=True).fillna(.5) + p6.rank(pct=True).fillna(.5) + rs.rank(pct=True).fillna(.5)
    specific_idx = set(prelim.nlargest(int(cfg["news"]["top_instruments_for_specific_news"])).index.tolist())
    instrument_jobs: dict[str, str] = {}
    for i in specific_idx:
        row = df.loc[i]
        isin = str(row.get("isin") or "")
        name = str(row.get("name") or "").strip()
        if isin and name:
            instrument_jobs[isin] = f'"{name}"'
    instrument_news = _parallel_news(instrument_jobs, cfg)

    # Stage 9 onward: sentiment and contextual gate, then passed downstream to
    # ETF structure, technical analysis and Smart Money.
    context_rows = []
    weights = cfg["context_weights"]
    for _, row in df.iterrows():
        code = _country_code(row.get("geo_exposure"), cfg)
        if code == "US":
            country_macro = us_score
        elif code in hicp:
            inf = hicp[code].get("inflation_score") if hicp[code].get("status") == "OK" else None
            country_macro = float(np.mean([x for x in [inf, eu_rate] if x is not None])) if inf is not None or eu_rate is not None else None
        elif code == "EU":
            country_macro = eu_score
        else:
            # Unsupported/stale country macro is not invented; only the global
            # backdrop is inherited and the context coverage reflects the gap.
            country_macro = None
        cnews = country_news.get(code, {}).get("score") if code != "GLOBAL" else global_news.get("score")
        cat = str(row.get("category") or "")
        vals = {
            "global_macro": global_macro,
            "country_macro": country_macro,
            "global_news": global_news.get("score"),
            "country_news": cnews,
            "sector_news": sector_news.get(cat, {}).get("score"),
            "instrument_news": instrument_news.get(str(row.get("isin") or ""), {}).get("score"),
            "market_sentiment": _sentiment_score(row),
        }
        score, coverage = _weighted_context(vals, weights)
        if score is None:
            multiplier, gate = 1.0, "DATA_REQUIRED"
        else:
            lo, hi = float(cfg["context_multiplier"]["min"]), float(cfg["context_multiplier"]["max"])
            raw_mult = lo + (hi - lo) * score / 100.0
            if coverage < float(cfg["minimum_context_coverage_for_positive_multiplier"]) and raw_mult > 1.0:
                raw_mult = 1.0
            multiplier = raw_mult
            gate = "BLOCK_BUY" if score < float(cfg["risk_gates"]["block_buy_below"]) else ("REVIEW_ONLY" if score < float(cfg["risk_gates"]["review_only_below"]) else "PASS")
        context_rows.append({
            "isin": row.get("isin"),
            "funnel_country_code": code,
            "funnel_global_macro_score": global_macro,
            "funnel_country_macro_score": country_macro,
            "funnel_global_news_score": global_news.get("score"),
            "funnel_country_news_score": cnews,
            "funnel_sector_news_score": sector_news.get(cat, {}).get("score"),
            "funnel_instrument_news_score": instrument_news.get(str(row.get("isin") or ""), {}).get("score"),
            "funnel_market_sentiment_score": _sentiment_score(row),
            "funnel_context_score": score,
            "funnel_context_coverage": round(coverage, 4),
            "funnel_macro_multiplier": round(multiplier, 4),
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
        "parallel_news_collection": True,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> None:
    result = apply()
    print("V20.5_FUNNEL_CONTEXT_FAST_OK", json.dumps({"global_macro": result["global_macro"]["score"], "coverage": result["mean_context_coverage"], "gates": result["risk_gates"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
