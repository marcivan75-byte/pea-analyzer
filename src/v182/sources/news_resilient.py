from __future__ import annotations

from datetime import datetime, timezone
import xml.etree.ElementTree as ET

import numpy as np
import requests

UA = "PEA-Analyzer-V21.1-News/1.1"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

# Materiality is deliberately an observable-title heuristic, not a new committee weight.
MATERIAL_TERMS = (
    "earnings", "results", "guidance", "profit warning", "warning", "forecast", "outlook",
    "merger", "acquisition", "takeover", "bid", "offer", "contract", "order", "partnership",
    "buyback", "share repurchase", "dividend", "capital increase", "rights issue", "placement",
    "default", "bankruptcy", "insolvency", "restructuring", "layoffs", "job cuts",
    "regulator", "regulatory", "investigation", "lawsuit", "fine", "sanction",
    "approval", "authorization", "patent", "clinical trial", "recall", "cyberattack",
)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def _title_metrics(titles: list[str], cfg: dict) -> dict:
    if not titles:
        return {
            "score": None,
            "polarity": None,
            "materiality_score": None,
            "material_articles": 0,
            "positive_term_hits": 0,
            "risk_term_hits": 0,
        }
    ncfg = cfg["news"]
    risk_terms = [str(x).lower() for x in ncfg["risk_terms"]]
    pos_terms = [str(x).lower() for x in ncfg["positive_terms"]]
    scores = []
    polarities = []
    material_article_scores = []
    pos_total = neg_total = 0
    for title in titles:
        t = str(title or "").lower()
        neg = sum(1 for term in risk_terms if term and term in t)
        pos = sum(1 for term in pos_terms if term and term in t)
        pos_total += pos
        neg_total += neg
        scores.append(_clip(50.0 + 12.0 * (pos - neg), 15.0, 85.0))
        denom = max(1, pos + neg)
        polarities.append(max(-1.0, min(1.0, (pos - neg) / denom)))
        material_hits = sum(1 for term in MATERIAL_TERMS if term in t)
        if material_hits:
            material_article_scores.append(min(100.0, 40.0 + 18.0 * material_hits))
        else:
            material_article_scores.append(15.0)

    material_articles = sum(1 for value in material_article_scores if value > 15.0)
    # Article count modestly increases confidence/materiality but cannot turn generic headlines
    # into a high-materiality event on its own.
    count_component = min(20.0, len(titles) * 0.75)
    materiality = min(100.0, float(np.mean(material_article_scores)) + count_component)
    return {
        "score": round(float(np.mean(scores)), 2),
        "polarity": round(float(np.mean(polarities)), 4),
        "materiality_score": round(materiality, 2),
        "material_articles": material_articles,
        "positive_term_hits": pos_total,
        "risk_term_hits": neg_total,
    }


def _result(query: str, titles: list[str], cfg: dict, source: str, source_mode: str) -> dict:
    metrics = _title_metrics(titles, cfg)
    return {
        "status": "OK" if titles else "NO_ARTICLES",
        **metrics,
        "articles": len(titles),
        "query": query,
        "source": source,
        "source_mode": source_mode,
        "materiality_method": "TITLE_EVENT_TERMS_PLUS_ARTICLE_COUNT_HEURISTIC_NOT_A_SCORE_WEIGHT",
        "polarity_method": "MEAN_POSITIVE_MINUS_RISK_TERM_BALANCE_-1_TO_1",
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _gdelt(query: str, cfg: dict) -> dict:
    ncfg = cfg["news"]
    response = requests.get(
        GDELT,
        params={
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": min(int(ncfg["max_records"]), 35),
            "sort": "HybridRel",
            "timespan": ncfg["timespan"],
        },
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=8,
    )
    response.raise_for_status()
    articles = response.json().get("articles", []) or []
    titles = [str(article.get("title") or "") for article in articles if article.get("title")]
    result = _result(query, titles, cfg, GDELT, "GDELT")
    result["domains"] = len({str(article.get("domain") or "") for article in articles if article.get("domain")})
    result["source_countries"] = len({str(article.get("sourcecountry") or "") for article in articles if article.get("sourcecountry")})
    return result


def _google_rss(query: str, cfg: dict) -> dict:
    response = requests.get(
        GOOGLE_NEWS_RSS,
        params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml"},
        timeout=10,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    titles = []
    for item in root.findall(".//item")[:35]:
        title = item.findtext("title")
        if title:
            titles.append(title)
    return _result(query, titles, cfg, GOOGLE_NEWS_RSS, "GOOGLE_NEWS_RSS_FALLBACK")


def news_score(query: str, cfg: dict) -> dict:
    errors = []
    try:
        result = _gdelt(query, cfg)
        if result["score"] is not None:
            return result
    except Exception as exc:
        errors.append(f"GDELT:{type(exc).__name__}:{str(exc)[:140]}")
    try:
        result = _google_rss(query, cfg)
        if result["score"] is not None:
            if errors:
                result["primary_error"] = errors[-1]
            return result
    except Exception as exc:
        errors.append(f"GOOGLE_RSS:{type(exc).__name__}:{str(exc)[:140]}")
    return {
        "status": "ERROR",
        "score": None,
        "polarity": None,
        "materiality_score": None,
        "material_articles": 0,
        "articles": 0,
        "query": query,
        "source": "GDELT+GOOGLE_NEWS_RSS",
        "source_mode": "ALL_FAILED",
        "errors": errors,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
    }
