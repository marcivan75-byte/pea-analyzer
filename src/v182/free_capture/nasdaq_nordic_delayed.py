from __future__ import annotations

import io
import os
import re
import time
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .core import CaptureStore, utcnow


SOURCE = "NASDAQ_NORDIC_DELAYED"
LANDING = "https://tradereports.nasdaq.com/shares/trade-reports/post-trade"
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def _norm(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _col(columns: list[str], aliases: list[str]) -> str | None:
    norms = {c: _norm(c) for c in columns}
    for alias in aliases:
        a = _norm(alias)
        for c, n in norms.items():
            if a == n or a in n:
                return c
    return None


def _discover(session: requests.Session, headers: dict[str, str]) -> tuple[list[str], str]:
    override = os.getenv("V211_NASDAQ_NORDIC_POSTTRADE_URL", "").strip()
    landing = override or LANDING
    r = session.get(landing, headers=headers, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(landing, str(a.get("href") or ""))
        text = " ".join(a.stripped_strings) + " " + href
        low = text.lower()
        if "nordicequity-posttrade" in low or low.endswith(".csv") or "download" in low and "post" in low:
            urls.append(href)

    # Some revisions of the page embed file paths in script data rather than anchor tags.
    for raw in re.findall(r"(?:https?://[^\"'<> ]+|/[A-Za-z0-9_./?=&%-]+)", r.text):
        low = raw.lower()
        if "nordicequity-posttrade" in low or ("post-trade" in low and ".csv" in low):
            urls.append(urljoin(landing, raw.replace("&amp;", "&")))

    seen: set[str] = set()
    out = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out, landing


def _read_csv(content: bytes) -> pd.DataFrame | None:
    for enc in ["utf-8-sig", "utf-8", "latin-1"]:
        for sep in [";", ",", "\t", "|"]:
            try:
                d = pd.read_csv(io.BytesIO(content), sep=sep, dtype=object, encoding=enc, low_memory=False)
                if len(d.columns) >= 4:
                    return d
            except Exception:
                continue
    return None


def _trade_rows(df: pd.DataFrame, universe_isins: set[str]) -> pd.DataFrame:
    cols = list(df.columns)
    isin_c = _col(cols, [
        "ISIN", "Financial Instrument Identification Code", "Instrument Identification Code",
        "Instrument ID", "InstrumentIdentifier",
    ])
    price_c = _col(cols, ["Price", "Trade Price", "Execution Price", "PriceMnt"])
    qty_c = _col(cols, ["Quantity", "Trade Quantity", "Volume", "Size", "Quantity in measurement unit"])
    time_c = _col(cols, [
        "Date and Time of the execution", "Transaction Time", "Trade Time", "Execution Time",
        "Publication Date and Time", "Timestamp", "DateTime",
    ])
    currency_c = _col(cols, ["Price Currency", "Currency"])
    if not isin_c or not price_c or not time_c:
        return pd.DataFrame()

    x = pd.DataFrame({
        "isin": df[isin_c].astype(str).str.strip().str.upper(),
        "price": pd.to_numeric(df[price_c].astype(str).str.replace(",", ".", regex=False), errors="coerce"),
        "qty": pd.to_numeric(df[qty_c].astype(str).str.replace(",", ".", regex=False), errors="coerce") if qty_c else 0.0,
        "ts": pd.to_datetime(df[time_c], errors="coerce", utc=True),
        "currency": df[currency_c].astype(str).str.strip().str.upper() if currency_c else "",
    }).dropna(subset=["price", "ts"])
    x = x[x["isin"].map(lambda s: bool(ISIN_RE.match(s)) and s in universe_isins)]
    x = x[x["price"].gt(0)]
    return x


def capture(universe: pd.DataFrame, store: CaptureStore, max_files: int = 120) -> dict:
    """Capture official 15-minute delayed Nordic post-trade files.

    Nasdaq creates minute files during market hours. We keep the download window bounded so the
    collector is courteous and incremental. Repeated runs build the daily/history cache; existing
    ISIN/date/source rows are replaced only by a fuller aggregation for the same key.
    """
    universe_isins = set(universe["isin"].astype(str).str.upper())
    session = requests.Session()
    headers = {
        "User-Agent": os.getenv("V182_USER_AGENT", "PEA-V21.1-FreeCapture/1.0"),
        "Accept-Language": "en-US,en;q=0.8",
    }
    try:
        urls, landing = _discover(session, headers)
    except Exception as exc:
        store.add_health(SOURCE, "DISCOVERY_ERROR", message=f"{type(exc).__name__}:{str(exc)[:500]}")
        return {"status": "DISCOVERY_ERROR", "discovered": 0, "downloaded": 0, "market_rows": 0}

    # Site lists newest files first; a bounded suffix is enough for an incremental wave.
    targets = urls[:max(0, max_files)]
    frames: list[pd.DataFrame] = []
    downloaded = failed = 0
    sample_urls: list[str] = []
    raw_root = store.root / "raw_official" / "nasdaq_nordic"
    raw_root.mkdir(parents=True, exist_ok=True)

    for url in targets:
        try:
            r = session.get(url, headers=headers, timeout=35)
            if not r.ok or len(r.content) < 20:
                failed += 1
                continue
            d = _read_csv(r.content)
            if d is None:
                failed += 1
                continue
            trades = _trade_rows(d, universe_isins)
            downloaded += 1
            if not trades.empty:
                frames.append(trades)
            if len(sample_urls) < 3:
                sample_urls.append(url)
            # Archive only the first few raw samples, not hundreds of minute files.
            if downloaded <= 3:
                name = re.sub(r"[^A-Za-z0-9._-]", "_", urlparse(url).path.rsplit("/", 1)[-1] or f"file_{downloaded}.csv")
                (raw_root / name).write_bytes(r.content)
        except Exception:
            failed += 1
        time.sleep(0.05)

    rows: list[dict] = []
    if frames:
        x = pd.concat(frames, ignore_index=True)
        x["date"] = x["ts"].dt.date.astype(str)
        x = x.sort_values(["isin", "ts"])
        for (isin, day), g in x.groupby(["isin", "date"], sort=False):
            cur = ""
            if "currency" in g and g["currency"].astype(str).str.len().gt(0).any():
                cur = str(g.loc[g["currency"].astype(str).str.len().gt(0), "currency"].iloc[-1])
            rows.append({
                "isin": isin,
                "date": day,
                "open": float(g["price"].iloc[0]),
                "high": float(g["price"].max()),
                "low": float(g["price"].min()),
                "close": float(g["price"].iloc[-1]),
                "volume": float(pd.to_numeric(g["qty"], errors="coerce").fillna(0).sum()),
                "currency": cur,
                "source": SOURCE,
                "ticker": "",
                "mic": "",
                "observed_at_utc": utcnow(),
            })

    added = store.upsert_market(rows)
    if added:
        status = "OK"
    elif urls:
        status = "FILES_FOUND_NO_UNIVERSE_TRADES" if downloaded else "FILES_DISCOVERED_DOWNLOAD_FAILED"
    else:
        status = "NO_FILE_LINKS_CURRENTLY_PUBLISHED"
    store.add_health(
        SOURCE,
        status,
        attempted=len(targets),
        succeeded=added,
        failed=failed,
        message=(
            f"landing={landing}; discovered={len(urls)}; downloaded={downloaded}; "
            f"frames={len(frames)}; sample_urls={sample_urls}"
        ),
    )
    return {
        "status": status,
        "discovered": len(urls),
        "attempted_files": len(targets),
        "downloaded": downloaded,
        "failed": failed,
        "market_rows": added,
        "sample_urls": sample_urls,
    }
