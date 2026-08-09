from __future__ import annotations

from datetime import date
from io import BytesIO
import os
import re
import zipfile

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .core import CaptureStore, clean_text, utcnow

PAGE = "https://www.gleif.org/en/lei-data/lei-mapping/download-isin-to-lei-relationship-files"


def _find_latest_zip(session: requests.Session) -> str:
    r = session.get(PAGE, timeout=30, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)
        m = re.search(r"isin-lei-(20\d{6}T\d+)\.zip", href + " " + text, re.I)
        if m:
            links.append((m.group(1), requests.compat.urljoin(PAGE, href)))
    return sorted(links, reverse=True)[0][1] if links else ""


def _read_mapping(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt"))]
        if not names:
            return pd.DataFrame(columns=["isin", "lei"])
        raw = zf.read(names[0])
    for sep in [",", ";", "\t", "|"]:
        try:
            df = pd.read_csv(BytesIO(raw), sep=sep, dtype=str, low_memory=False)
            if len(df.columns) < 2:
                continue
            norm = {c: re.sub(r"[^A-Z0-9]", "", str(c).upper()) for c in df.columns}
            isin_c = next((c for c, n in norm.items() if n == "ISIN" or "ISIN" in n), None)
            lei_c = next((c for c, n in norm.items() if n == "LEI" or n.endswith("LEI")), None)
            if isin_c and lei_c:
                out = df[[isin_c, lei_c]].rename(columns={isin_c: "isin", lei_c: "lei"})
                out["isin"] = out["isin"].astype(str).str.strip().str.upper()
                out["lei"] = out["lei"].astype(str).str.strip().str.upper()
                return out[out["isin"].str.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", na=False) & out["lei"].str.len().eq(20)]
        except Exception:
            continue
    return pd.DataFrame(columns=["isin", "lei"])


def capture(universe: pd.DataFrame, store: CaptureStore) -> dict:
    today = date.today().isoformat()
    existing = store.identity()
    if not existing.empty:
        cached = existing[(existing["source"].eq("GLEIF_ISIN_LEI_BULK")) & (existing["as_of"].astype(str).eq(today))]
        covered = cached.loc[cached["lei"].astype(str).str.len().eq(20), "isin"].astype(str).nunique()
        if covered >= int(len(universe) * 0.95):
            store.add_health("GLEIF_ISIN_LEI_BULK", "CACHED_TODAY", len(universe), covered, len(universe)-covered,
                             message="Daily bulk relationship file already represented in restored cache")
            return {"status": "CACHED_TODAY", "resolved": covered, "added": 0, "mapping_rows": "CACHED"}

    session = requests.Session()
    try:
        url = _find_latest_zip(session)
        if not url:
            raise RuntimeError("latest GLEIF ISIN-LEI zip not discovered")
        r = session.get(url, timeout=90, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
        r.raise_for_status()
        mapping = _read_mapping(r.content)
    except Exception as exc:
        store.add_health("GLEIF_ISIN_LEI_BULK", "ERROR", message=str(exc))
        return {"status": "ERROR", "resolved": 0, "error": str(exc)}

    wanted = universe[["isin", "name"]].copy()
    wanted["isin"] = wanted["isin"].astype(str).str.upper()
    hit = wanted.merge(mapping.drop_duplicates("isin", keep="last"), on="isin", how="inner")
    rows = [{
        "isin": r.isin, "name": clean_text(r.name), "source": "GLEIF_ISIN_LEI_BULK",
        "ticker": "", "exchange": "", "mic": "", "figi": "", "composite_figi": "",
        "share_class_figi": "", "security_type": "", "lei": r.lei, "lei_source": "GLEIF_ANNA_BULK",
        "resolution_status": "CERTIFIED_BULK", "as_of": today, "observed_at_utc": utcnow(),
    } for r in hit.itertuples(index=False)]
    added = store.upsert_identity(rows)
    store.add_health("GLEIF_ISIN_LEI_BULK", "OK" if added else "NO_NEW_DATA", len(universe), len(hit), len(universe)-len(hit),
                     message=f"mapping_rows={len(mapping)}; file={url.split('/')[-1]}")
    return {"status": "OK", "mapping_rows": len(mapping), "resolved": len(hit), "added": added,
            "file": url.split('/')[-1]}
