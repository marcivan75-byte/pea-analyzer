from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse
import gzip
import io
import os
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .core import CaptureStore, clean_text, number, utcnow
from .nasdaq_nordic_delayed import capture as capture_nasdaq_nordic

SOURCES = {
    "EURONEXT_DELAYED": "https://marketdata.euronext.com/data-reporting-service/trades-file",
    "DEUTSCHE_BOERSE_DELAYED": "https://www.mds.deutsche-boerse.com/mds-en/real-time-data/Delayed-data",
}


def _discover(session: requests.Session, source: str, landing: str) -> list[str]:
    override = os.getenv(f"V211_{source}_URL", "").strip()
    if override:
        return [override]
    r = session.get(landing, timeout=30, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(landing, a["href"])
        text = (a.get_text(" ", strip=True) + " " + href).lower()
        if any(x in text for x in [".csv", ".gz", "download", "delayed", "post-trade", "posttrade"]):
            urls.append(href)
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))][:25]


def _read_table(content: bytes, url: str) -> pd.DataFrame | None:
    try:
        raw = gzip.decompress(content) if url.lower().endswith(".gz") else content
    except OSError:
        raw = content
    for sep in [";", ",", "\t", "|"]:
        try:
            df = pd.read_csv(io.BytesIO(raw), sep=sep, dtype=object, low_memory=False)
            if len(df.columns) >= 4:
                return df
        except Exception:
            pass
    return None


def _col(columns: list[str], patterns: list[str]) -> str | None:
    norm = {c: re.sub(r"[^A-Z0-9]", "", str(c).upper()) for c in columns}
    for p in patterns:
        pn = re.sub(r"[^A-Z0-9]", "", p.upper())
        for c, n in norm.items():
            if pn == n or pn in n:
                return c
    return None


def _aggregate(df: pd.DataFrame, source: str) -> list[dict]:
    cols = list(df.columns)
    isin_c = _col(cols, ["ISIN", "FINANCIALINSTRUMENTIDENTIFICATIONCODE", "INSTRUMENTID"])
    price_c = _col(cols, ["PRICE", "TRADEPRICE", "EXECUTIONPRICE"])
    qty_c = _col(cols, ["QUANTITY", "TRADEQUANTITY", "SIZE", "VOLUME"])
    time_c = _col(cols, ["TRADETIME", "TRANSACTIONTIME", "TIMESTAMP", "EXECUTIONTIME", "DATEANDTIME"])
    if not isin_c or not price_c or not time_c:
        return []
    x = pd.DataFrame({
        "isin": df[isin_c].astype(str).str.strip(),
        "price": pd.to_numeric(df[price_c], errors="coerce"),
        "qty": pd.to_numeric(df[qty_c], errors="coerce") if qty_c else 0.0,
        "ts": pd.to_datetime(df[time_c], errors="coerce", utc=True),
    }).dropna(subset=["price", "ts"])
    x = x[x["isin"].str.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", na=False)]
    if x.empty:
        return []
    x["date"] = x["ts"].dt.date.astype(str)
    x = x.sort_values(["isin", "ts"])
    out: list[dict] = []
    for (isin, day), g in x.groupby(["isin", "date"], sort=False):
        out.append({
            "isin": isin, "date": day, "open": g["price"].iloc[0], "high": g["price"].max(),
            "low": g["price"].min(), "close": g["price"].iloc[-1], "volume": g["qty"].sum(),
            "currency": "", "source": source, "ticker": "", "mic": "", "observed_at_utc": utcnow(),
        })
    return out


def _capture_legacy_official(store: CaptureStore) -> dict:
    session = requests.Session()
    raw_root = store.root / "raw_official"
    raw_root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict] = {}
    for source, landing in SOURCES.items():
        try:
            urls = _discover(session, source, landing)
        except Exception as exc:
            store.add_health(source, "DISCOVERY_ERROR", message=str(exc))
            summary[source] = {"status": "DISCOVERY_ERROR", "files": 0, "market_rows": 0}
            continue
        market_rows: list[dict] = []
        downloaded = 0
        for url in urls[:8]:
            if not any(urlparse(url).path.lower().endswith(ext) for ext in [".csv", ".gz", ".txt"]):
                continue
            try:
                r = session.get(url, timeout=45, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
                if not r.ok or len(r.content) < 50:
                    continue
                downloaded += 1
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(urlparse(url).path).name or f"file_{downloaded}")
                (raw_root / f"{source}_{safe}").write_bytes(r.content)
                df = _read_table(r.content, url)
                if df is not None:
                    market_rows.extend(_aggregate(df, source))
            except Exception:
                continue
        added = store.upsert_market(market_rows)
        status = "OK" if added else ("FILES_ARCHIVED_NO_OHLCV" if downloaded else "NO_DIRECT_FILE_DISCOVERED")
        store.add_health(source, status, len(urls), added, max(0, len(urls) - downloaded), message=f"downloaded={downloaded}")
        summary[source] = {"status": status, "discovered": len(urls), "downloaded": downloaded, "market_rows": added}
    return summary


def capture(store: CaptureStore) -> dict:
    """Capture every official free delayed market lane currently automatable.

    Euronext/Deutsche Börse retain their generic discovery path. Nasdaq Nordic has a dedicated
    collector because its post-trade service publishes minute CSV files. The universe is recovered
    from the already validated identity cache, so the main orchestrator remains unchanged.
    """
    summary = _capture_legacy_official(store)
    identity = store.identity()
    if identity.empty:
        nasdaq = {"status": "NO_IDENTITY_CACHE", "market_rows": 0}
        store.add_health("NASDAQ_NORDIC_DELAYED", "NO_IDENTITY_CACHE")
    else:
        universe = pd.DataFrame({"isin": sorted(set(identity["isin"].astype(str)))})
        nasdaq = capture_nasdaq_nordic(
            universe,
            store,
            max_files=int(os.getenv("V211_NASDAQ_NORDIC_MAX_FILES", "120")),
        )
    summary["NASDAQ_NORDIC_DELAYED"] = nasdaq
    return summary
