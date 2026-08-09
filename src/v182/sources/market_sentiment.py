from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import json
import re
import time
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "outputs/audit/V20.4.2_MARKET_SENTIMENT.json"
TARGETS = [
    ROOT / "outputs/V20.4_GITOK_ACTIONS_1829_DECISIONS.csv",
    ROOT / "outputs/V20.4_GITOK_ETF_266_DECISIONS.csv",
    ROOT / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv",
]
CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNN_FALLBACK_URL = "https://raw.githubusercontent.com/whit3rabbit/fear-greed-data/main/fear-greed.csv"
AAII_BASE = "https://insights.aaii.com"
UA = "Mozilla/5.0 (compatible; PEA-Analyzer-V20.4.2/1.0; +https://github.com/)"


def _request(url: str, *, accept: str = "*/*") -> requests.Response:
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": accept}, timeout=15)
            r.raise_for_status()
            return r
        except Exception as exc:  # pragma: no cover - network dependent
            last = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"market sentiment request failed: {url}: {last}")


def _as_utc(value) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            v = float(value)
            if v > 10_000_000_000:
                v /= 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc)
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None


def _age_days(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def _cnn_fear_greed() -> dict:
    try:
        payload = _request(CNN_URL, accept="application/json").json()
        item = payload.get("fear_and_greed") or {}
        score = float(item["score"])
        if not 0 <= score <= 100:
            raise ValueError("CNN score outside 0..100")
        asof = _as_utc(item.get("timestamp"))
        age = _age_days(asof)
        if age is not None and age > 4.5:
            raise ValueError(f"CNN Fear & Greed stale: {age:.1f} days")
        return {
            "score": round(score, 2),
            "rating": str(item.get("rating") or "").strip(),
            "asof": asof.isoformat() if asof else datetime.now(timezone.utc).isoformat(),
            "source": CNN_URL,
            "source_mode": "CNN_LIVE",
            "age_days": round(age, 3) if age is not None else None,
        }
    except Exception as live_exc:
        r = _request(CNN_FALLBACK_URL, accept="text/csv")
        df = pd.read_csv(StringIO(r.text))
        required = {"Date", "Fear Greed"}
        if not required.issubset(df.columns) or df.empty:
            raise RuntimeError(f"CNN fallback invalid after live failure: {live_exc}")
        last = df.iloc[-1]
        asof = _as_utc(str(last["Date"]))
        age = _age_days(asof)
        if age is None or age > 4.5:
            raise RuntimeError(f"CNN fallback stale after live failure: {live_exc}; age={age}")
        score = float(last["Fear Greed"])
        return {
            "score": round(score, 2),
            "rating": str(last.get("Rating", "")).strip(),
            "asof": asof.isoformat(),
            "source": CNN_FALLBACK_URL,
            "source_mode": "CNN_FALLBACK_FRESH",
            "age_days": round(age, 3),
            "live_error": str(live_exc),
        }


def _extract_aaii_article(url: str) -> dict | None:
    r = _request(url, accept="text/html")
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True).replace("–", "-").replace("−", "-")

    def value(label: str) -> float | None:
        # Prefer a single paragraph so a historical-average number from a later
        # sentence can never be mistaken for the current survey result.
        for node in soup.find_all(["p", "li"]):
            t = node.get_text(" ", strip=True).replace("–", "-").replace("−", "-")
            if not re.match(rf"^\s*{label}\s+sentiment\b", t, flags=re.I):
                continue
            m = re.search(r"percentage points to\s+([0-9]+(?:\.[0-9]+)?)%", t, flags=re.I)
            if m:
                return float(m.group(1))
        # Sentence-bounded fallback only; do not cross into another sentiment line.
        m = re.search(
            rf"\b{label}\s+sentiment\b[^.]*?percentage points to\s+([0-9]+(?:\.[0-9]+)?)%",
            text,
            flags=re.I,
        )
        return float(m.group(1)) if m else None

    bull, neutral, bear = value("Bullish"), value("Neutral"), value("Bearish")
    spread = None
    for node in soup.find_all(["p", "li"]):
        t = node.get_text(" ", strip=True).replace("–", "-").replace("−", "-")
        if "bull-bear spread" not in t.lower():
            continue
        m = re.search(r"percentage points to\s+(-?[0-9]+(?:\.[0-9]+)?)%", t, flags=re.I)
        if m:
            spread = float(m.group(1)); break
    if spread is None:
        m = re.search(r"bull-bear spread[^.]*?percentage points to\s+(-?[0-9]+(?:\.[0-9]+)?)%", text, flags=re.I)
        spread = float(m.group(1)) if m else (bull - bear if bull is not None and bear is not None else None)
    if bull is None or neutral is None or bear is None or spread is None:
        return None

    # Internal arithmetic gate: official spread should reconcile with bull-bear.
    if abs((bull - bear) - spread) > 0.15:
        raise RuntimeError(f"AAII arithmetic mismatch: bull={bull}, bear={bear}, spread={spread}")

    asof = None
    meta = soup.find("meta", attrs={"property": "article:published_time"})
    if meta and meta.get("content"):
        asof = _as_utc(meta.get("content"))
    if asof is None:
        time_tag = soup.find("time")
        if time_tag and time_tag.get("datetime"):
            asof = _as_utc(time_tag.get("datetime"))
    if asof is None:
        m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+20\d{2}\b", text)
        if m:
            asof = _as_utc(m.group(0))
    age = _age_days(asof)
    if age is None or age > 14.5:
        return None
    return {
        "bullish_pct": round(bull, 2),
        "neutral_pct": round(neutral, 2),
        "bearish_pct": round(bear, 2),
        "bull_bear_spread": round(spread, 2),
        "asof": asof.isoformat(),
        "source": url,
        "source_mode": "AAII_OFFICIAL",
        "age_days": round(age, 3),
    }


def _aaii_sentiment() -> dict:
    now = datetime.now(timezone.utc)
    errors: list[str] = []
    for year in (now.year, now.year - 1):
        sitemap_url = f"{AAII_BASE}/sitemap/{year}"
        try:
            soup = BeautifulSoup(_request(sitemap_url, accept="text/html").text, "html.parser")
            urls: list[str] = []
            for a in soup.find_all("a", href=True):
                title = a.get_text(" ", strip=True).lower()
                href = str(a["href"])
                if "aaii sentiment survey" in title or "/p/aaii-sentiment-survey" in href:
                    u = urljoin(AAII_BASE, href)
                    if u not in urls:
                        urls.append(u)
            for url in urls[:10]:
                try:
                    item = _extract_aaii_article(url)
                    if item is not None:
                        item["sitemap"] = sitemap_url
                        return item
                except Exception as exc:
                    errors.append(f"{url}: {exc}")
        except Exception as exc:
            errors.append(f"{sitemap_url}: {exc}")
    raise RuntimeError("AAII current survey unavailable: " + " | ".join(errors[-4:]))


def collect_market_sentiment() -> dict:
    fg = _cnn_fear_greed()
    aaii = _aaii_sentiment()
    status = "LIVE" if fg["source_mode"] == "CNN_LIVE" else "DEGRADED_FRESH"
    return {
        "status": status,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "fear_greed": fg,
        "aaii": aaii,
    }


def overlay_market_sentiment() -> dict:
    sentiment = collect_market_sentiment()
    fg, aaii = sentiment["fear_greed"], sentiment["aaii"]
    values = {
        "fear_greed_index": fg["score"],
        "fear_greed_rating": fg["rating"],
        "fear_greed_asof": fg["asof"],
        "fear_greed_source": fg["source"],
        "aaii_bullish_pct": aaii["bullish_pct"],
        "aaii_neutral_pct": aaii["neutral_pct"],
        "aaii_bearish_pct": aaii["bearish_pct"],
        "aaii_bull_bear_spread": aaii["bull_bear_spread"],
        "aaii_asof": aaii["asof"],
        "aaii_source": aaii["source"],
        "sentiment_data_status": sentiment["status"],
        "sentiment_collected_at_utc": sentiment["collected_at_utc"],
    }
    updated: dict[str, int] = {}
    for path in TARGETS:
        if not path.exists():
            raise RuntimeError(f"sentiment overlay target missing: {path}")
        df = pd.read_csv(path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
        for key, val in values.items():
            df[key] = val
        df.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
        updated[path.name] = len(df)
    sentiment["updated_files"] = updated
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(sentiment, indent=2, ensure_ascii=False), encoding="utf-8")
    return sentiment


def main() -> None:
    result = overlay_market_sentiment()
    print("V20.4.2_MARKET_SENTIMENT_OK", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
