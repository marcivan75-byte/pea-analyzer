from __future__ import annotations

import os
import time
import requests
import pandas as pd

from .core import CaptureStore, clean_text, utcnow

URL = "https://api.openfigi.com/v3/mapping"


def _pick_result(items: list[dict], canonical_ticker: str, canonical_mic: str) -> tuple[dict | None, str]:
    equities = [x for x in items if str(x.get("marketSector", "")).lower() == "equity"] or items
    if not equities:
        return None, "NO_MATCH"
    ticker = clean_text(canonical_ticker).split(".")[0].upper()
    mic = clean_text(canonical_mic).upper()
    exact = [x for x in equities if clean_text(x.get("ticker")).upper() == ticker]
    pool = exact or equities
    if mic:
        mic_hits = [x for x in pool if clean_text(x.get("exchCode")).upper() == mic]
        if mic_hits:
            pool = mic_hits
    status = "UNIQUE" if len(pool) == 1 else "AMBIGUOUS_BEST_EFFORT"
    return pool[0], status


def capture(universe: pd.DataFrame, store: CaptureStore, max_requests: int = 30) -> dict:
    key = os.getenv("OPENFIGI_API_KEY", "").strip()
    batch_size = 100 if key else 5
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key

    existing = store.identity()
    done = set(existing.loc[existing["source"].eq("OPENFIGI_V3"), "isin"].astype(str)) if not existing.empty else set()
    targets = universe.loc[~universe["isin"].astype(str).isin(done)].copy()
    targets = targets.head(max_requests * batch_size)
    rows: list[dict] = []
    failed = 0
    requests_used = 0
    session = requests.Session()

    for start in range(0, len(targets), batch_size):
        chunk = targets.iloc[start:start + batch_size]
        payload = [{"idType": "ID_ISIN", "idValue": str(v)} for v in chunk["isin"]]
        try:
            r = session.post(URL, json=payload, headers=headers, timeout=30)
            requests_used += 1
            if r.status_code == 429:
                wait = float(r.headers.get("ratelimit-reset", "6") or 6)
                time.sleep(max(1.0, min(wait, 15.0)))
                r = session.post(URL, json=payload, headers=headers, timeout=30)
                requests_used += 1
            r.raise_for_status()
            answers = r.json()
        except Exception as exc:
            failed += len(chunk)
            store.add_health("OPENFIGI_V3", "ERROR", len(chunk), 0, len(chunk), requests_used, "", str(exc))
            continue

        for (_, src), answer in zip(chunk.iterrows(), answers, strict=False):
            items = answer.get("data") or []
            chosen, resolution = _pick_result(items, src.get("yahoo_ticker", ""), src.get("euronext_mic", ""))
            if not chosen:
                failed += 1
                continue
            rows.append({
                "isin": str(src["isin"]), "name": clean_text(src.get("name")), "source": "OPENFIGI_V3",
                "ticker": clean_text(chosen.get("ticker")), "exchange": clean_text(chosen.get("exchCode")),
                "mic": clean_text(src.get("euronext_mic")), "figi": clean_text(chosen.get("figi")),
                "composite_figi": clean_text(chosen.get("compositeFIGI")),
                "share_class_figi": clean_text(chosen.get("shareClassFIGI")),
                "security_type": clean_text(chosen.get("securityType2") or chosen.get("securityType")),
                "resolution_status": resolution, "as_of": pd.Timestamp.utcnow().date().isoformat(),
                "observed_at_utc": utcnow(),
            })
        if requests_used >= max_requests:
            break
        time.sleep(0.30 if key else 2.5)

    added = store.upsert_identity(rows)
    store.add_health("OPENFIGI_V3", "OK" if added else "NO_NEW_DATA", len(targets), added, failed,
                     requests_used, "", f"key={'YES' if key else 'NO'} batch={batch_size}")
    return {"attempted": len(targets), "added": added, "failed": failed, "requests": requests_used, "key": bool(key)}
