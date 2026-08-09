from __future__ import annotations

from datetime import datetime, timezone
import math
import xml.etree.ElementTree as ET

import numpy as np
import requests

UA = "PEA-Analyzer-V20.5-News/1.0"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def _score_titles(titles: list[str], cfg: dict) -> float | None:
    if not titles:
        return None
    ncfg = cfg["news"]
    risk_terms = [x.lower() for x in ncfg["risk_terms"]]
    pos_terms = [x.lower() for x in ncfg["positive_terms"]]
    values = []
    for title in titles:
        t = str(title or "").lower()
        neg = sum(1 for term in risk_terms if term in t)
        pos = sum(1 for term in pos_terms if term in t)
        values.append(_clip(50.0 + 12.0 * (pos - neg), 15.0, 85.0))
    return round(float(np.mean(values)), 2) if values else None


def _gdelt(query: str, cfg: dict) -> dict:
    ncfg = cfg["news"]
    r = requests.get(
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
    r.raise_for_status()
    articles = r.json().get("articles", []) or []
    titles = [str(a.get("title") or "") for a in articles if a.get("title")]
    return {
        "status": "OK" if titles else "NO_ARTICLES",
        "score": _score_titles(titles, cfg),
        "articles": len(titles),
        "query": query,
        "source": GDELT,
        "source_mode": "GDELT",
    }


def _google_rss(query: str, cfg: dict) -> dict:
    r = requests.get(
        GOOGLE_NEWS_RSS,
        params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml"},
        timeout=10,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)
    titles = []
    for item in root.findall(".//item")[:35]:
        title = item.findtext("title")
        if title:
            titles.append(title)
    return {
        "status": "OK" if titles else "NO_ARTICLES",
        "score": _score_titles(titles, cfg),
        "articles": len(titles),
        "query": query,
        "source": GOOGLE_NEWS_RSS,
        "source_mode": "GOOGLE_NEWS_RSS_FALLBACK",
    }


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
        "articles": 0,
        "query": query,
        "source": "GDELT+GOOGLE_NEWS_RSS",
        "source_mode": "ALL_FAILED",
        "errors": errors,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
    }
